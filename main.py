"""CLI entry point for managing optical frames inventory.

Usage (interactive):
	python main.py add        # prompts for fields
	python main.py search     # prompts for search filters
	python main.py init-db    # (re)create tables

Data is stored locally in SQLite at output/frames.db
Telegram bot integration lives in telegram_bot.py
Set TELEGRAM_BOT_TOKEN environment variable before running the bot module.
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

from db import Base, SessionLocal, engine
from models import Frame, MATERIAL_CHOICES


def init_db(drop: bool = False):
	if drop:
		Base.metadata.drop_all(bind=engine)
	Base.metadata.create_all(bind=engine)
	print("Database initialized.")


def prompt_add_frame() -> Frame:
	print("Enter frame data (leave blank to skip / use defaults).")
	def ask(label: str, required: bool = False, cast=lambda x: x, default=None):
		while True:
			raw = input(f"{label}{' *' if required else ''}: ").strip()
			if not raw:
				if required and default is None:
					print("This field is required.")
					continue
				return default
			try:
				return cast(raw)
			except Exception as e:  # noqa: BLE001
				print(f"Invalid value: {e}")

	brand = ask("Brand (can be blank)") or None
	model_code = ask("Model code", required=True)
	material = ask(
		f"Material {MATERIAL_CHOICES} (blank = Unknown)",
		cast=lambda v: v.lower(),
		default="unknown",
	) or "unknown"
	lens_width = ask("Lens width (mm)", cast=int, default=None)
	bridge_size = ask("Bridge size (mm)", cast=int, default=None)
	temple_length = ask("Temple length (mm)", cast=int, default=None)
	color = ask("Color", default=None)
	shape = ask("Shape (e.g., round, square)", default=None)
	gender = ask("Gender (men/women/unisex/child)", default=None)
	price = ask("Price", cast=float, default=None)
	stock = ask("Stock quantity", cast=int, default=0)
	notes = ask("Notes", default=None)

	frame = Frame(
		brand=brand,
		model_code=model_code,
		material=material,
		lens_width=lens_width,
		bridge_size=bridge_size,
		temple_length=temple_length,
		color=color,
		shape=shape,
		gender=gender,
		price=price,
		stock=stock,
		notes=notes,
		created_at=datetime.utcnow(),
	)
	return frame


def add_frame(frame: Frame):
	with SessionLocal() as session:
		session.add(frame)
		session.commit()
		session.refresh(frame)
		print(f"Added frame ID={frame.id} -> {frame.brand or 'NoBrand'} {frame.model_code}")


def prompt_search() -> Dict[str, Any]:
	print("Enter search filters (blank to skip). Supports partial matches for text.")
	filters: Dict[str, Any] = {}
	def maybe_field(name: str, key: str | None = None, cast=lambda x: x):
		val = input(f"{name}: ").strip()
		if val:
			try:
				filters[key or name] = cast(val)
			except Exception as e:  # noqa: BLE001
				print(f"Skipping invalid {name}: {e}")
	maybe_field("brand")
	maybe_field("model_code")
	maybe_field("material", cast=lambda v: v.lower())
	maybe_field("color")
	maybe_field("shape")
	maybe_field("gender")
	maybe_field("min_price", key="min_price", cast=float)
	maybe_field("max_price", key="max_price", cast=float)
	maybe_field("lens_width", key="lens_width", cast=int)
	return filters


def search_frames(filters: Dict[str, Any]):
	from sqlalchemy import and_, or_  # local import
	with SessionLocal() as session:
		query = session.query(Frame)
		text_fields = ["brand", "model_code", "material", "color", "shape", "gender"]
		for field in text_fields:
			if field in filters:
				value = f"%{filters[field]}%"
				query = query.filter(getattr(Frame, field).ilike(value))
		if "lens_width" in filters:
			query = query.filter(Frame.lens_width == filters["lens_width"])
		if "min_price" in filters:
			query = query.filter(Frame.price >= filters["min_price"])
		if "max_price" in filters:
			query = query.filter(Frame.price <= filters["max_price"])
		results = query.order_by(Frame.created_at.desc()).limit(100).all()
		if not results:
			print("No frames found.")
			return
		for f in results:
			print(
				f"ID={f.id} | {f.brand or 'NoBrand'} {f.model_code} | {f.material} | "
				f"{f.lens_width or '-'}-{f.bridge_size or '-'}-{f.temple_length or '-'} | "
				f"Color: {f.color or '-'} | Stock: {f.stock} | Price: {f.price or '-'}"
			)


def main():
	parser = argparse.ArgumentParser(description="Optical frames inventory manager")
	sub = parser.add_subparsers(dest="cmd")
	sub.add_parser("init-db")
	sub.add_parser("add")
	sub.add_parser("search")
	parser.add_argument("--drop", action="store_true", help="Drop existing tables when init-db")
	args = parser.parse_args()

	if args.cmd == "init-db":
		init_db(drop=args.drop)
	elif args.cmd == "add":
		frame = prompt_add_frame()
		add_frame(frame)
	elif args.cmd == "search":
		filters = prompt_search()
		search_frames(filters)
	else:
		parser.print_help()


if __name__ == "__main__":  # pragma: no cover
	main()

