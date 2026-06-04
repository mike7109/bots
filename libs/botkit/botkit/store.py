"""Tiny SQLite ledger for idempotency / dedup across runs.

Cron starts a fresh process each day, so "did I already send this?" has to
survive restarts — it lives in a file on a mounted volume, not in memory.

The whole API is one table, `sent(kind, key, day)`:

    store = Store()
    if not store.already_sent("overdue", issue_id):
        ...send...
        store.mark_sent("overdue", issue_id)

`day` defaults to today, so a reminder fires at most once per item per day:
a second cron run today is a no-op, but tomorrow it can remind again (e.g. an
issue that is still overdue). Digests and nudges will reuse the same ledger —
keep keys namespaced by `kind` so they don't collide.
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from pathlib import Path

from botkit.config import env

DEFAULT_DB = "/data/state.db"  # mounted volume in deploy/docker-compose.yml


class Store:
    def __init__(self, path: str | None = None):
        self.path = path or env("STATE_DB", DEFAULT_DB)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path)
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS sent ("
            " kind TEXT NOT NULL,"
            " key  TEXT NOT NULL,"
            " day  TEXT NOT NULL,"
            " ts   TEXT NOT NULL,"
            " PRIMARY KEY (kind, key, day))"
        )
        self._db.commit()

    @staticmethod
    def _today() -> str:
        return dt.date.today().isoformat()

    def already_sent(self, kind: str, key: str | int, *, day: str | None = None) -> bool:
        cur = self._db.execute(
            "SELECT 1 FROM sent WHERE kind=? AND key=? AND day=?",
            (kind, str(key), day or self._today()),
        )
        return cur.fetchone() is not None

    def mark_sent(self, kind: str, key: str | int, *, day: str | None = None) -> None:
        self._db.execute(
            "INSERT OR IGNORE INTO sent (kind, key, day, ts) VALUES (?,?,?,?)",
            (kind, str(key), day or self._today(),
             dt.datetime.now().isoformat(timespec="seconds")),
        )
        self._db.commit()

    def close(self) -> None:
        self._db.close()
