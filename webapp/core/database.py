"""SQLite connection and inspection history schema."""

import sqlite3
import threading
from pathlib import Path

from .. import config

_conn: sqlite3.Connection | None = None
_lock = threading.Lock()


def get_db_conn() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is None:
            _conn = sqlite3.connect(str(config.DB_PATH), check_same_thread=False)
            _conn.row_factory = sqlite3.Row
        return _conn


def init_db() -> None:
    conn = get_db_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS inspection_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            lot_number TEXT,
            mfg_name TEXT,
            mfg_part_number TEXT,
            material TEXT,
            ball_diameter TEXT,
            ball_diameter_mm REAL,
            customer_name TEXT,
            inspection_result TEXT,
            total_balls INTEGER,
            good_balls INTEGER,
            bad_balls INTEGER,
            no_balls INTEGER,
            composite_image_path TEXT
        )
        """
    )
    conn.commit()
