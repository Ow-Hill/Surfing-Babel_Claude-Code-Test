"""SQLite + filesystem storage for scanned stats and found images."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS finds (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    seed        TEXT NOT NULL,
    image_file  TEXT NOT NULL,
    image_hash  TEXT NOT NULL UNIQUE,
    source_url  TEXT,
    page_url    TEXT,
    score       REAL NOT NULL,
    label       TEXT NOT NULL,
    metrics     TEXT NOT NULL,
    found_at    REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS stats (
    key   TEXT PRIMARY KEY,
    value INTEGER NOT NULL
);
"""


class Store:
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.image_dir = self.data_dir / "images"
        self.image_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.data_dir / "babel.db",
                                   check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._db.commit()

    # ---- stats -----------------------------------------------------------
    def incr(self, key: str, n: int = 1) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO stats(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = value + excluded.value",
                (key, n))
            self._db.commit()

    def stats(self) -> dict:
        with self._lock:
            rows = self._db.execute("SELECT key, value FROM stats").fetchall()
            finds = self._db.execute("SELECT COUNT(*) FROM finds").fetchone()[0]
        out = {r["key"]: r["value"] for r in rows}
        out["finds"] = finds
        return out

    # ---- finds -----------------------------------------------------------
    def save_find(self, *, seed: str, image_bytes: bytes, ext: str,
                  score: float, label: str, metrics: dict,
                  source_url: str | None = None,
                  page_url: str | None = None) -> int | None:
        """Save an image + seed. Returns the new id, or None if a duplicate."""
        digest = hashlib.sha256(image_bytes).hexdigest()
        ext = ext.lstrip(".").lower() or "png"
        name = f"{digest[:24]}.{ext}"
        with self._lock:
            exists = self._db.execute(
                "SELECT id FROM finds WHERE image_hash = ?", (digest,)
            ).fetchone()
            if exists:
                return None
            (self.image_dir / name).write_bytes(image_bytes)
            cur = self._db.execute(
                "INSERT INTO finds(seed, image_file, image_hash, source_url, "
                "page_url, score, label, metrics, found_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (seed, name, digest, source_url, page_url, score, label,
                 json.dumps(metrics), time.time()))
            self._db.commit()
            return cur.lastrowid

    def list_finds(self, limit: int = 200, offset: int = 0) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM finds ORDER BY found_at DESC LIMIT ? OFFSET ?",
                (limit, offset)).fetchall()
        return [self._row(r) for r in rows]

    def get_find(self, find_id: int) -> dict | None:
        with self._lock:
            r = self._db.execute("SELECT * FROM finds WHERE id = ?",
                                 (find_id,)).fetchone()
        return self._row(r) if r else None

    def delete_find(self, find_id: int) -> bool:
        with self._lock:
            r = self._db.execute("SELECT image_file FROM finds WHERE id = ?",
                                 (find_id,)).fetchone()
            if not r:
                return False
            self._db.execute("DELETE FROM finds WHERE id = ?", (find_id,))
            self._db.commit()
        (self.image_dir / r["image_file"]).unlink(missing_ok=True)
        return True

    @staticmethod
    def _row(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["metrics"] = json.loads(d["metrics"])
        return d
