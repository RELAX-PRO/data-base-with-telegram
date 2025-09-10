# Optical Frames Inventory (Python + SQLite + Telegram Bot)

A lightweight local inventory system for your optical store. Stores ~1000+ frames with brand, model, sizes, material, etc., using SQLite (no manual SQL needed—handled by SQLAlchemy). Includes:

1. Command Line Interface (CLI) in `main.py` for manual add/search.
2. Telegram bot in `telegram_bot.py` for remote adding & searching.

## Features
- Add frames with brand, model code, material, sizes (lens/bridge/temple), color, shape, gender, price, stock, notes.
- Optional brand (can be blank / unknown).
- Search by partial text (brand, model, color...) and numeric filters (price range, lens width).
- SQLite database stored at `output/frames.db`.
- Telegram commands: `/add`, `/search`, `/get`.

## Install
Create a virtual environment (recommended) and install dependencies:
```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Initialize Database
```powershell
python main.py init-db
```
(Add `--drop` to recreate tables.)

## Add a Frame (CLI)
```powershell
python main.py add
```
Follow the prompts.

## Search (CLI)
```powershell
python main.py search
```
Enter filters (blank to skip). Supports: brand, model_code, material, color, shape, gender, min_price, max_price, lens_width.

## Telegram Bot
You do NOT need a separate server. The script uses long polling: as long as the script is running, it receives messages. Close the window = bot pauses. Run it again any time.

### Get a Bot Token
1. Open Telegram, talk to @BotFather.
2. Send: `/newbot` and follow prompts.
3. Copy the token it gives (looks like `123456789:AA...`).

### Put the Token (2 simple options)
Option A (easiest): Create a file `bot_token.txt` in the same folder as `telegram_bot.py` and paste ONLY the token inside.

Option B: Set environment variable (temporary for current PowerShell window):
```powershell
$env:TELEGRAM_BOT_TOKEN = "8452155169:AAGOO285faxdAmAFpCr1Dj2G65hPl55nI38"
```

### Run the Bot
```powershell
python telegram_bot.py
```
Leave it running while you use it. Press Ctrl+C to stop.
### Bot Commands Examples
```
/add brand=RayBan model=RB1234 material=plastic lens=52 bridge=18 temple=140 color=black price=120 stock=5
/search brand=ray material=plastic color=black
/get 1
```

## Data Fields
| Field | Description |
|-------|-------------|
| brand | Optional brand name |
| model_code | Frame model identifier (required) |
| material | e.g. plastic, titanium, metal, etc. |
| lens_width | mm |
| bridge_size | mm |
| temple_length | mm |
| color | Color description |
| shape | Round, square, etc. |
| gender | men, women, unisex, child |
| price | Numeric price |
| stock | Quantity in stock |
| notes | Extra text |

## Backup
The database is a single file `output/frames.db`. Back it up by copying that file.

## Next Ideas
- Export to CSV/Excel.
- Add photos (store file path column).
- User auth for bot.
- Simple FastAPI web dashboard.

Enjoy managing your inventory!
