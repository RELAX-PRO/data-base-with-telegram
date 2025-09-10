"""Telegram bot interface for inventory.

Commands:
    /start  -> help
    /add brand=... model=... material=... lens=52 bridge=18 temple=140 color=black price=120 stock=5
    /search brand=ray material=plastic color=black
    /get 5

Token lookup order:
    1. Environment variable TELEGRAM_BOT_TOKEN
    2. File bot_token.txt in same directory

Set BOT_DEBUG=1 for diagnostic prints if token not detected.
"""
from __future__ import annotations

import os
import shlex
import logging
import io
from collections import Counter
from sqlalchemy import func, text, desc
from datetime import datetime
from typing import Dict, Any

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from db import SessionLocal, engine, Base
from models import Frame, MATERIAL_CHOICES


def init_db():
    Base.metadata.create_all(bind=engine)
    # Attempt to create a unique index for (brand, model_code) (case-insensitive) if no duplicates.
    try:
        with engine.connect() as conn:
            dup = conn.execute(text(
                """
                SELECT lower(COALESCE(brand,'')) b, lower(model_code) m, COUNT(*) c
                FROM frames GROUP BY b,m HAVING c>1 LIMIT 1;
                """
            )).fetchone()
            if dup is None:
                try:
                    conn.execute(text(
                        "CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_brand_model ON frames (LOWER(COALESCE(brand,'')), LOWER(model_code));"
                    ))
                except Exception as e:  # noqa: BLE001
                    print(f"[WARN] Could not create unique index: {e}")
            else:
                print("[INFO] Skipping unique index; duplicates exist. Use /duplicates and /merge or /delete to clean.")
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] Unique index check failed: {e}")


def parse_kv_args(text: str) -> Dict[str, str]:
    parts = shlex.split(text)
    data: Dict[str, str] = {}
    for part in parts:
        if "=" in part:
            k, v = part.split("=", 1)
            data[k.lower()] = v
    return data


def build_help_text() -> str:
    return (
        "Optical Inventory Bot\n\n"
        "QUICK ADD:\n"
        "/new  – guided add (answers step by step)\n"
        "/add model=CODE brand=Brand stock=2 material=plastic lens=52 bridge=18 temple=140 color=black price=120\n"
        "( /add merges with existing brand+model, increasing stock )\n\n"
        "EDIT / STOCK:\n"
        "/update <id> field=value ... | /setstock <id> <n> | /merge <source_id> <target_id> | /delete <id>\n\n"
        "LOOKUP:\n"
    "/get <id> | /recent [n] (latest added) | /list [n] (by id asc) | /brand <brand> | /search brand=... color=... min_price=.. max_price=..\n"
        "/duplicates | /lowstock [th] | /stats | /count\n\n"
        "DATA EXPORT:\n"
    "/export [n] (CSV) or /export format=json limit=100 brand=Ray since=2025-01-01\n"
    "Formats: format=csv|json|text|txt (default csv). Filters: limit=, brand=, since=YYYY-MM-DD.\n"
    "/backup (raw DB)\n\n"
        "ALIAS SHORTCUTS:\n"
        "/ls -> /recent  |  /inv -> /stats  |  /c -> /count\n\n"
        "MISC:\n"
    "/ping | /help | /help fields (list and explain all fields)\n\n"
        "FIELDS (use as key=value in /add or /update):\n"
        "model/model_code, brand, material, lens(lens_width), bridge(bridge_size), temple(temple_length), color, shape, gender, price, stock, notes.\n\n"
        "Tips: Put spaces inside quotes: color=\"matte black\"   notes=\"spring hinge\".\n"
        "Use /new if you forget the syntax – it's conversational."
    )


HELP_TEXT = build_help_text()


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Support /help fields
    if context.args and context.args[0].lower() == "fields":
        await update.message.reply_text(
            "Field meanings:\n"
            "model/model_code: Your internal or manufacturer code (required).\n"
            "brand: Brand / label (can be empty).\n"
            "material: Material of frame (e.g., plastic, metal). Unknown defaults to 'unknown'.\n"
            "lens(lens_width): Lens width in mm.\n"
            "bridge(bridge_size): Bridge size (mm).\n"
            "temple(temple_length): Temple arm length (mm).\n"
            "color: Color or finish (text).\n"
            "shape: Frame shape (round, rectangular, cat-eye, etc).\n"
            "gender: Intended audience label (men, women, unisex, kids). Optional.\n"
            "price: Numeric price (float).\n"
            "stock: Current quantity on hand (int).\n"
            "notes: Any free text notes.\n\n"
            "You can set multiple in one command: /add model=AB12 brand=Ray lens=52 bridge=18 temple=140 color=black price=120 stock=3"
        )
        return
    await update.message.reply_text(build_help_text())


async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):  # simple health check
    await update.message.reply_text("pong")

async def count_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with SessionLocal() as session:
        total = session.query(Frame).count()
        # basic material distribution (top 5)
        rows = session.query(Frame.material).all()
        materials = Counter([r[0] or 'unknown' for r in rows])
        top = ", ".join(f"{m}:{c}" for m, c in materials.most_common(5)) or "(none)"
    await update.message.reply_text(f"Total frames: {total}\nTop materials: {top}")


async def recent_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /recent or /recent 10
    limit = 5
    if context.args:
        try:
            limit = max(1, min(50, int(context.args[0])))
        except ValueError:
            pass
    with SessionLocal() as session:
        rows = (
            session.query(Frame)
            .order_by(Frame.created_at.desc())
            .limit(limit)
            .all()
        )
    if not rows:
        await update.message.reply_text("No frames yet.")
        return
    lines = [
        f"{r.id}: {r.brand or 'NoBrand'} {r.model_code} {r.material} "
        f"{r.lens_width or '-'}-{r.bridge_size or '-'}-{r.temple_length or '-'} stock={r.stock}"
        for r in rows
    ]
    await update.message.reply_text("\n".join(lines))


async def export_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Enhanced export: /export [limit] OR /export format=json limit=200 brand=Ray since=2025-01-01
    raw_args = context.args
    format_type = "csv"
    limit = 100
    brand_filter = None
    since_date = None
    # Quick form: single integer
    if len(raw_args) == 1 and raw_args[0].isdigit():
        limit = max(1, min(2000, int(raw_args[0])))
    else:
        kv_text = " ".join(raw_args)
        kv = parse_kv_args(kv_text)
        if "format" in kv:
            format_type = kv["format"].lower()
        if "limit" in kv and kv["limit"].isdigit():
            limit = max(1, min(2000, int(kv["limit"])))
        if "brand" in kv:
            brand_filter = kv["brand"].lower()
        if "since" in kv:
            try:
                since_date = datetime.strptime(kv["since"], "%Y-%m-%d")
            except ValueError:
                pass
    with SessionLocal() as session:
        query = session.query(Frame)
        if brand_filter:
            query = query.filter(func.lower(Frame.brand) == brand_filter)
        if since_date:
            query = query.filter(Frame.created_at >= since_date)
        rows = (
            query.order_by(Frame.created_at.desc()).limit(limit).all()
        )
    if not rows:
        await update.message.reply_text("No data to export with given filters.")
        return
    headers = [
        "id","brand","model_code","material","lens_width","bridge_size","temple_length","color","shape","gender","price","stock","notes","created_at"
    ]
    if format_type == "json":
        import json
        data = [r.to_dict() for r in rows]
        buf = io.StringIO()
        json.dump(data, buf, ensure_ascii=False, indent=2, default=str)
        buf.seek(0)
        await update.message.reply_document(document=buf, filename=f"frames_export_{len(rows)}.json")
        return
    if format_type in ("text", "txt"):
        lines = [
            f"{r.id}: {(r.brand or 'NoBrand')} {r.model_code} stock={r.stock} price={r.price or '-'}" for r in rows
        ]
        out = "\n".join(lines)
        # Always produce a .txt file if user explicitly wants text/txt OR if long
        if format_type == "txt" or len(out) > 3500:
            buf = io.StringIO(out)
            buf.seek(0)
            await update.message.reply_document(document=buf, filename=f"frames_export_{len(rows)}.txt")
        else:
            await update.message.reply_text(out)
        return
    # default CSV
    buf = io.StringIO()
    buf.write(",".join(headers) + "\n")
    def esc(v):
        if v is None:
            return ""
        s = str(v)
            # escape quotes/newlines/commas
        if any(ch in s for ch in [',','"','\n']):
            s = '"' + s.replace('"','""') + '"'
        return s
    for r in rows:
        d = r.to_dict()
        buf.write(",".join(esc(d[h]) for h in headers) + "\n")
    buf.seek(0)
    await update.message.reply_document(document=buf, filename=f"frames_export_{len(rows)}.csv")


help_cmd = start  # alias


FIELD_MAP = {
    "brand": str,
    "model": ("model_code", str),
    "model_code": str,
    "material": str,
    "lens": ("lens_width", int),
    "lens_width": int,
    "bridge": ("bridge_size", int),
    "bridge_size": int,
    "temple": ("temple_length", int),
    "temple_length": int,
    "color": str,
    "shape": str,
    "gender": str,
    "price": float,
    "stock": int,
    "notes": str,
}


def normalize_fields(kv: Dict[str, str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for k, raw in kv.items():
        if k not in FIELD_MAP:
            continue
        spec = FIELD_MAP[k]
        if isinstance(spec, tuple):
            field, caster = spec
        else:
            field, caster = k, spec
        try:
            out[field] = caster(raw)
        except Exception:  # noqa: BLE001
            continue
    if "material" in out and out["material"].lower() not in MATERIAL_CHOICES:
        out["material"] = "unknown"
    return out


async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.partition(" ")[2]
    data = parse_kv_args(text)
    norm = normalize_fields(data)
    if "model_code" not in norm:
        await update.message.reply_text("Missing model (use model=<code>)")
        return
    with SessionLocal() as session:
        # Deduplication rule: same (brand, model_code) case-insensitive. Could be extended.
        brand_val = (norm.get("brand") or "").lower()
        model_val = norm["model_code"].lower()
        existing = (
            session.query(Frame)
            .filter(func.lower(Frame.model_code) == model_val)
            .filter(func.coalesce(func.lower(Frame.brand), "") == brand_val)
            .first()
        )
        if existing:
            old_stock = existing.stock
            add_stock = norm.get("stock", 0) or 0
            # Increase stock by provided stock (or +1 if none supplied)
            if add_stock == 0:
                add_stock = 1
            existing.stock = (existing.stock or 0) + add_stock
            # Fill missing fields only (don't overwrite existing non-empty)
            updatable_fields = [
                "material","lens_width","bridge_size","temple_length","color","shape","gender","price","notes"
            ]
            for f in updatable_fields:
                if getattr(existing, f) in (None, "", 0) and f in norm and norm[f] not in (None, ""):
                    setattr(existing, f, norm[f])
            session.commit()
            await update.message.reply_text(
                f"✅ Updated existing ID={existing.id}\nStock: {old_stock} -> {existing.stock}\n"
                f"Item: {(existing.brand or 'NoBrand')} {existing.model_code}"
            )
        else:
            frame = Frame(**norm)
            frame.created_at = datetime.utcnow()
            if "stock" not in norm:
                frame.stock = 1  # default at least 1 if not given
            session.add(frame)
            session.commit()
            session.refresh(frame)
            await update.message.reply_text(
                f"🆕 Added ID={frame.id}\nItem: {(frame.brand or 'NoBrand')} {frame.model_code}\nStock={frame.stock}"
            )


# ------------------ Conversational /new flow ------------------

NEW_MODEL, NEW_BRAND, NEW_STOCK, NEW_OPTIONALS, NEW_CONFIRM = range(5)


async def new_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["new_frame"] = {}
    await update.message.reply_text("Creating new frame. What is the model code? (or /cancel)")
    return NEW_MODEL


async def new_model(update: Update, context: ContextTypes.DEFAULT_TYPE):
    model = update.message.text.strip()
    if not model:
        await update.message.reply_text("Please provide a non-empty model code.")
        return NEW_MODEL
    context.user_data["new_frame"]["model_code"] = model
    await update.message.reply_text("Brand? (send '-' to leave empty)")
    return NEW_BRAND


async def new_brand(update: Update, context: ContextTypes.DEFAULT_TYPE):
    brand = update.message.text.strip()
    if brand == "-":
        brand = None
    context.user_data["new_frame"]["brand"] = brand
    await update.message.reply_text("Initial stock? (number, default 1)")
    return NEW_STOCK


async def new_stock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    stock = 1
    if txt:
        try:
            stock = max(0, int(txt))
        except ValueError:
            await update.message.reply_text("Not a number. Try again (or 1).")
            return NEW_STOCK
    context.user_data["new_frame"]["stock"] = stock
    await update.message.reply_text(
        "Optional fields? Send a line like: material=plastic lens=52 bridge=18 temple=140 color=black price=120 notes=Nice\n" \
        "Or send 'done' to finish without extras."
    )
    return NEW_OPTIONALS


async def new_optionals(update: Update, context: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text.strip()
    if txt.lower() != "done":
        kv = parse_kv_args(txt)
        norm = normalize_fields(kv)
        context.user_data["new_frame"].update(norm)
        # allow more rounds
        await update.message.reply_text("Add more fields or 'done'.")
        return NEW_OPTIONALS
    # Confirm summary
    data = context.user_data.get("new_frame", {})
    lines = [f"{k}={v}" for k, v in data.items()]
    await update.message.reply_text(
        "Summary:\n" + "\n".join(lines) + "\nType 'yes' to save, 'no' to cancel, or edit more fields first."
    )
    return NEW_CONFIRM


async def new_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ans = update.message.text.strip().lower()
    if ans not in ("yes", "y", "no", "n"):
        await update.message.reply_text("Please answer yes or no.")
        return NEW_CONFIRM
    if ans in ("no", "n"):
        context.user_data.pop("new_frame", None)
        await update.message.reply_text("Cancelled.")
        return ConversationHandler.END
    data = context.user_data.pop("new_frame", {})
    # Reuse /add logic (dedupe + merge)
    with SessionLocal() as session:
        brand_val = (data.get("brand") or "").lower()
        model_val = data["model_code"].lower()
        existing = (
            session.query(Frame)
            .filter(func.lower(Frame.model_code) == model_val)
            .filter(func.coalesce(func.lower(Frame.brand), "") == brand_val)
            .first()
        )
        if existing:
            old_stock = existing.stock
            existing.stock = (existing.stock or 0) + (data.get("stock") or 0 or 1)
            for f in [
                "material","lens_width","bridge_size","temple_length","color","shape","gender","price","notes"
            ]:
                if getattr(existing, f) in (None, "", 0) and f in data:
                    setattr(existing, f, data[f])
            session.commit()
            await update.message.reply_text(
                f"Merged into existing ID={existing.id}. Stock {old_stock}->{existing.stock}"
            )
        else:
            frame = Frame(**data)
            frame.created_at = datetime.utcnow()
            session.add(frame)
            session.commit()
            session.refresh(frame)
            await update.message.reply_text(f"Saved new ID={frame.id}")
    return ConversationHandler.END


async def new_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("new_frame", None)
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END


def make_new_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CommandHandler("new", new_start)],
        states={
            NEW_MODEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, new_model)],
            NEW_BRAND: [MessageHandler(filters.TEXT & ~filters.COMMAND, new_brand)],
            NEW_STOCK: [MessageHandler(filters.TEXT & ~filters.COMMAND, new_stock)],
            NEW_OPTIONALS: [MessageHandler(filters.TEXT & ~filters.COMMAND, new_optionals)],
            NEW_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, new_confirm)],
        },
        fallbacks=[CommandHandler("cancel", new_cancel)],
        name="new_frame_conv",
        persistent=False,
    )



async def delete_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /delete <id>
    if not context.args:
        await update.message.reply_text("Usage: /delete <id>")
        return
    try:
        _id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Invalid id")
        return
    with SessionLocal() as session:
        row = session.get(Frame, _id)
        if not row:
            await update.message.reply_text("Not found")
            return
        session.delete(row)
        session.commit()
    await update.message.reply_text(f"Deleted ID={_id}")


async def setstock_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /setstock <id> <number>
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /setstock <id> <number>")
        return
    try:
        _id = int(context.args[0]); val = int(context.args[1])
    except ValueError:
        await update.message.reply_text("Numbers only")
        return
    with SessionLocal() as session:
        row = session.get(Frame, _id)
        if not row:
            await update.message.reply_text("Not found")
            return
        old = row.stock
        row.stock = val
        session.commit()
    await update.message.reply_text(f"Stock ID={_id} {old}->{val}")


async def update_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /update <id> field=value field=value
    if not context.args:
        await update.message.reply_text("Usage: /update <id> field=value ...")
        return
    try:
        _id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("First arg must be id")
        return
    kv_text = " ".join(context.args[1:])
    kv = parse_kv_args(kv_text)
    norm = normalize_fields(kv)
    if not norm:
        await update.message.reply_text("No valid fields to update.")
        return
    with SessionLocal() as session:
        row = session.get(Frame, _id)
        if not row:
            await update.message.reply_text("Not found")
            return
        for k, v in norm.items():
            setattr(row, k, v)
        session.commit()
    await update.message.reply_text(f"Updated ID={_id} fields: {', '.join(norm.keys())}")


async def duplicates_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # List potential duplicates by (brand, model_code) pair counts >1
    with SessionLocal() as session:
        rows = (
            session.query(Frame.brand, Frame.model_code, func.count(Frame.id))
            .group_by(Frame.brand, Frame.model_code)
            .having(func.count(Frame.id) > 1)
            .order_by(func.count(Frame.id).desc())
            .limit(20)
            .all()
        )
    if not rows:
        await update.message.reply_text("No duplicates detected.")
        return
    lines = [f"{(b or 'NoBrand')} {m} -> {c} entries" for b, m, c in rows]
    await update.message.reply_text("Possible duplicates:\n" + "\n".join(lines))


async def lowstock_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /lowstock [threshold]
    threshold = 5
    if context.args:
        try:
            threshold = max(0, int(context.args[0]))
        except ValueError:
            pass
    with SessionLocal() as session:
        rows = (
            session.query(Frame)
            .filter(Frame.stock <= threshold)
            .order_by(Frame.stock.asc())
            .limit(50)
            .all()
        )
    if not rows:
        await update.message.reply_text(f"No items with stock <= {threshold}.")
        return
    lines = [f"{r.id}:{r.brand or 'NoBrand'} {r.model_code} stock={r.stock}" for r in rows]
    await update.message.reply_text("Low stock (<= %d):\n" % threshold + "\n".join(lines))


async def stats_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with SessionLocal() as session:
        total = session.query(func.count(Frame.id)).scalar() or 0
        distinct_brands = session.query(func.count(func.distinct(Frame.brand))).scalar() or 0
        avg_price = session.query(func.avg(Frame.price)).scalar()
        avg_price_txt = f"{avg_price:.2f}" if avg_price else "-"
        total_stock = session.query(func.sum(Frame.stock)).scalar() or 0
        # Top brand by frame count
        top_by_count = (
            session.query(Frame.brand, func.count(Frame.id).label("cnt"))
            .group_by(Frame.brand)
            .order_by(desc("cnt"))
            .first()
        )
        top_by_count_txt = (
            f"{top_by_count[0] or 'NoBrand'} ({top_by_count[1]} frames)" if top_by_count else "-"
        )
        # Top brand by total stock units
        top_by_stock = (
            session.query(Frame.brand, func.sum(Frame.stock).label("s"))
            .group_by(Frame.brand)
            .order_by(desc("s"))
            .first()
        )
        top_by_stock_txt = (
            f"{top_by_stock[0] or 'NoBrand'} ({int(top_by_stock[1])} units)" if top_by_stock else "-"
        )
        avg_stock_per_frame = f"{(total_stock/total):.2f}" if total else "-"
    await update.message.reply_text(
        "Stats:\n"
        f"Frames: {total}\n"
        f"Brands: {distinct_brands}\n"
        f"Top Brand (frames): {top_by_count_txt}\n"
        f"Top Brand (stock): {top_by_stock_txt}\n"
        f"Total Stock Units: {total_stock}\n"
        f"Avg Stock / Frame: {avg_stock_per_frame}\n"
        f"Avg Price: {avg_price_txt}"
    )


async def list_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /list or /list 20 (id ascending) optional limit
    limit = 10
    if context.args:
        try:
            limit = max(1, min(100, int(context.args[0])))
        except ValueError:
            pass
    with SessionLocal() as session:
        rows = (
            session.query(Frame)
            .order_by(Frame.id.asc())
            .limit(limit)
            .all()
        )
    if not rows:
        await update.message.reply_text("No frames.")
        return
    lines = [f"{r.id}: {(r.brand or 'NoBrand')} {r.model_code} stock={r.stock}" for r in rows]
    await update.message.reply_text("First %d by ID:\n" % len(rows) + "\n".join(lines))


async def brand_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /brand <brand_name>")
        return
    name = " ".join(context.args)
    with SessionLocal() as session:
        rows = (
            session.query(Frame)
            .filter(func.lower(Frame.brand) == name.lower())
            .order_by(Frame.model_code.asc())
            .limit(25)
            .all()
        )
    if not rows:
        await update.message.reply_text("No frames for that brand.")
        return
    lines = [
        f"{r.id}: {r.model_code} stock={r.stock} price={r.price or '-'}" for r in rows
    ]
    await update.message.reply_text(f"Brand {name} (first {len(rows)}):\n" + "\n".join(lines))


async def backup_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Send the raw SQLite file
    db_path = os.path.join(os.path.dirname(__file__), 'output', 'frames.db')
    if not os.path.isfile(db_path):
        await update.message.reply_text("No database file found.")
        return
    try:
        with open(db_path, 'rb') as f:
            await update.message.reply_document(f, filename='frames_backup.db')
    except Exception as e:  # noqa: BLE001
        await update.message.reply_text(f"Backup failed: {e}")


async def merge_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # /merge <source_id> <target_id>
    if len(context.args) < 2:
        await update.message.reply_text("Usage: /merge <source_id> <target_id>")
        return
    try:
        sid = int(context.args[0]); tid = int(context.args[1])
    except ValueError:
        await update.message.reply_text("IDs must be numbers")
        return
    if sid == tid:
        await update.message.reply_text("Source and target must differ.")
        return
    with SessionLocal() as session:
        src = session.get(Frame, sid)
        tgt = session.get(Frame, tid)
        if not src or not tgt:
            await update.message.reply_text("Source or target not found")
            return
        tgt.stock = (tgt.stock or 0) + (src.stock or 0)
        fields = ["material","lens_width","bridge_size","temple_length","color","shape","gender","price","notes","brand"]
        for f in fields:
            if getattr(tgt, f) in (None, "", 0) and getattr(src, f) not in (None, ""):
                setattr(tgt, f, getattr(src, f))
        new_stock = tgt.stock
        session.delete(src)
        session.commit()
    await update.message.reply_text(f"Merged {sid} into {tid}. New stock={new_stock}")


async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.partition(" ")[2]
    data = parse_kv_args(text)
    norm = normalize_fields(data)
    # numeric price filters
    min_price = None
    max_price = None
    if "min_price" in data:
        try:
            min_price = float(data["min_price"])
        except ValueError:
            pass
    if "max_price" in data:
        try:
            max_price = float(data["max_price"])
        except ValueError:
            pass
    with SessionLocal() as session:
        query = session.query(Frame)
        for field, value in norm.items():
            col = getattr(Frame, field)
            if isinstance(value, str):
                query = query.filter(col.ilike(f"%{value}%"))
            else:
                query = query.filter(col == value)
        if min_price is not None:
            query = query.filter(Frame.price >= min_price)
        if max_price is not None:
            query = query.filter(Frame.price <= max_price)
        rows = query.order_by(Frame.created_at.desc()).limit(10).all()
        if not rows:
            await update.message.reply_text("No matches.")
            return
        lines = [
            f"{r.id}: {r.brand or 'NoBrand'} {r.model_code} {r.material} "
            f"{r.lens_width or '-'}-{r.bridge_size or '-'}-{r.temple_length or '-'} stock={r.stock}"
            for r in rows
        ]
        await update.message.reply_text("\n".join(lines))


async def get_one(update: Update, context: ContextTypes.DEFAULT_TYPE):
    parts = update.message.text.strip().split()
    if len(parts) < 2:
        await update.message.reply_text("Usage: /get <id>")
        return
    try:
        _id = int(parts[1])
    except ValueError:
        await update.message.reply_text("Invalid id")
        return
    with SessionLocal() as session:
        row = session.get(Frame, _id)
        if not row:
            await update.message.reply_text("Not found")
            return
        d = row.to_dict()
        out = "\n".join(f"{k}: {v}" for k, v in d.items())
        await update.message.reply_text(out)


def load_token() -> str:
    """Load bot token; print diagnostics if BOT_DEBUG=1."""
    debug = os.environ.get("BOT_DEBUG") == "1"
    env_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if debug:
        print(f"[DEBUG] ENV present: {bool(env_token)}")
    if env_token:
        t = env_token.strip()
        if t:
            if debug:
                print(f"[DEBUG] Using env token prefix: {t[:8]}")
            return t
    file_path = os.path.join(os.path.dirname(__file__), "bot_token.txt")
    if debug:
        print(f"[DEBUG] Looking for file: {file_path}")
    if os.path.isfile(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            token_file = f.read().strip()
        if debug:
            print(f"[DEBUG] File exists, length={len(token_file)}")
        if token_file:
            return token_file
    raise SystemExit("Bot token not found. Set TELEGRAM_BOT_TOKEN or create bot_token.txt")


def main():
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=logging.INFO,
    )
    token = load_token()
    logging.info("Starting bot with token prefix %s***", token[:8])
    init_db()
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", start))  # dynamic help
    app.add_handler(make_new_handler())
    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(CommandHandler("lowstock", lowstock_cmd))
    app.add_handler(CommandHandler("stats", stats_cmd))
    app.add_handler(CommandHandler("brand", brand_cmd))
    app.add_handler(CommandHandler("backup", backup_cmd))
    app.add_handler(CommandHandler("merge", merge_cmd))
    app.add_handler(CommandHandler("add", add))
    app.add_handler(CommandHandler("search", search))
    app.add_handler(CommandHandler("get", get_one))
    app.add_handler(CommandHandler("count", count_cmd))
    app.add_handler(CommandHandler("recent", recent_cmd))
    app.add_handler(CommandHandler("list", list_cmd))
    app.add_handler(CommandHandler("export", export_cmd))
    # Aliases
    app.add_handler(CommandHandler("ls", recent_cmd))
    app.add_handler(CommandHandler("inv", stats_cmd))
    app.add_handler(CommandHandler("c", count_cmd))
    app.add_handler(CommandHandler("delete", delete_cmd))
    app.add_handler(CommandHandler("setstock", setstock_cmd))
    app.add_handler(CommandHandler("update", update_cmd))
    app.add_handler(CommandHandler("duplicates", duplicates_cmd))
    logging.info("Bot running. Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":  # pragma: no cover
    main()
