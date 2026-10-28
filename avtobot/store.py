"""Plate history. The same plate is not fetched again until it is a day old."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

FRESH_SECONDS = 24 * 60 * 60


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS lookups (
            id INTEGER PRIMARY KEY,
            user_id INTEGER NOT NULL,
            plate TEXT NOT NULL,
            title TEXT NOT NULL,
            card TEXT NOT NULL,
            fetched_at INTEGER NOT NULL,
            asked_at INTEGER NOT NULL
        )
        """
    )
    db.execute("CREATE INDEX IF NOT EXISTS lookups_plate ON lookups (plate, fetched_at)")
    db.execute("CREATE INDEX IF NOT EXISTS lookups_user ON lookups (user_id, asked_at)")
    return db


def fresh(db: sqlite3.Connection, plate: str, now: float | None = None) -> tuple[str, str, int] | None:
    row = db.execute(
        "SELECT title, card, fetched_at FROM lookups WHERE plate = ? ORDER BY fetched_at DESC LIMIT 1",
        (plate,),
    ).fetchone()
    if row is None:
        return None
    moment = time.time() if now is None else now
    if moment - int(row[2]) > FRESH_SECONDS:
        return None
    return str(row[0]), str(row[1]), int(row[2])


def remember(
    db: sqlite3.Connection,
    user_id: int,
    plate: str,
    title: str,
    card: str,
    fetched_at: int,
) -> None:
    db.execute(
        "INSERT INTO lookups (user_id, plate, title, card, fetched_at, asked_at) VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, plate, title, card, fetched_at, int(time.time())),
    )
    db.commit()


def history(db: sqlite3.Connection, user_id: int, limit: int = 10) -> list[tuple[str, str, int]]:
    rows = db.execute(
        """
        SELECT plate, title, asked_at FROM lookups
        WHERE user_id = ? AND id IN (
            SELECT MAX(id) FROM lookups WHERE user_id = ? GROUP BY plate
        )
        ORDER BY asked_at DESC
        LIMIT ?
        """,
        (user_id, user_id, limit),
    ).fetchall()
    return [(str(plate), str(title), int(asked)) for plate, title, asked in rows]


def last_plate(db: sqlite3.Connection, user_id: int) -> str | None:
    row = db.execute(
        "SELECT plate FROM lookups WHERE user_id = ? ORDER BY asked_at DESC LIMIT 1",
        (user_id,),
    ).fetchone()
    if row is None:
        return None
    return str(row[0])
