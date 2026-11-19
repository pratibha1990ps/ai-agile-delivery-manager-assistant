"""SQLite connection helpers."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(path: str | Path) -> sqlite3.Connection:
    """Open a connection with foreign keys on and dict-like rows. Use ':memory:' for tests."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()


def reset_db(conn: sqlite3.Connection) -> None:
    """Drop every table and recreate the schema. Destroys all data."""
    conn.execute("PRAGMA foreign_keys = OFF")
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    for table in tables:
        conn.execute(f'DROP TABLE IF EXISTS "{table}"')
    conn.commit()
    conn.execute("PRAGMA foreign_keys = ON")
    init_db(conn)


def resolve_db_path(configured: Path) -> Path:
    """`ADM_DB_PATH` overrides the configured path (used by tests and demos)."""
    override = os.environ.get("ADM_DB_PATH")
    return Path(override) if override else configured


def open_app_db(configured: Path) -> sqlite3.Connection:
    conn = connect(resolve_db_path(configured))
    init_db(conn)
    return conn
