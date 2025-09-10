"""Database setup (SQLite) and session factory."""
from __future__ import annotations

from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

DB_DIR = Path("output")
DB_DIR.mkdir(exist_ok=True)
DB_PATH = DB_DIR / "frames.db"
DATABASE_URL = f"sqlite:///{DB_PATH}"  # file-based sqlite

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},  # needed for SQLite + threads (telegram bot)
    echo=False,
    future=True,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

Base = declarative_base()


def get_session():
    """Context manager helper for sessions."""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:  # noqa: BLE001
        session.rollback()
        raise
    finally:
        session.close()
