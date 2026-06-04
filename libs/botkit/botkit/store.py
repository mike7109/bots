"""Tiny SQLite store for idempotency / dedup and small state snapshots.

Cron starts a fresh process each day, so anything it needs to remember between
runs has to survive restarts — it lives in a file on a mounted volume, not in
memory. Two tables, both namespaced by `kind` so callers don't collide:

  sent(kind, key, day)   a one-shot ledger: "did I already send this today?"

    if not store.already_sent("overdue", issue_id):
        ...send...; store.mark_sent("overdue", issue_id)

  state(kind, key)       an arbitrary JSON snapshot keyed by (kind, key)

    prev = store.get_state("digest_personal", login)   # last snapshot or None
    ...compute delta vs prev...; store.set_state("digest_personal", login, cur)

`day` defaults to today, so a reminder fires at most once per item per day: a
second cron run today is a no-op, but tomorrow it can remind again. The state
table powers delta digests — only notify when the snapshot actually changed.
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
import threading
from pathlib import Path

from botkit.config import env

DEFAULT_DB = "/data/state.db"  # mounted volume in deploy/docker-compose.yml


class Store:
    def __init__(self, path: str | None = None):
        self.path = path or env("STATE_DB", DEFAULT_DB)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False: the webhook app shares one Store across
        # FastAPI's threadpool workers; a lock serialises access.
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._lock = threading.Lock()
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS sent ("
            " kind TEXT NOT NULL,"
            " key  TEXT NOT NULL,"
            " day  TEXT NOT NULL,"
            " ts   TEXT NOT NULL,"
            " PRIMARY KEY (kind, key, day))"
        )
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS state ("
            " kind    TEXT NOT NULL,"
            " key     TEXT NOT NULL,"
            " value   TEXT NOT NULL,"
            " updated TEXT NOT NULL,"
            " PRIMARY KEY (kind, key))"
        )
        self._db.commit()

    @staticmethod
    def _today() -> str:
        return dt.date.today().isoformat()

    def already_sent(self, kind: str, key: str | int, *, day: str | None = None) -> bool:
        with self._lock:
            cur = self._db.execute(
                "SELECT 1 FROM sent WHERE kind=? AND key=? AND day=?",
                (kind, str(key), day or self._today()),
            )
            return cur.fetchone() is not None

    def mark_sent(self, kind: str, key: str | int, *, day: str | None = None) -> None:
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO sent (kind, key, day, ts) VALUES (?,?,?,?)",
                (kind, str(key), day or self._today(),
                 dt.datetime.now().isoformat(timespec="seconds")),
            )
            self._db.commit()

    # --- state snapshots (JSON value per kind/key) -----------------------
    def get_state(self, kind: str, key: str | int):
        """Return the stored JSON snapshot for (kind, key), or None if unset."""
        with self._lock:
            cur = self._db.execute(
                "SELECT value FROM state WHERE kind=? AND key=?", (kind, str(key))
            )
            row = cur.fetchone()
        return json.loads(row[0]) if row else None

    def set_state(self, kind: str, key: str | int, value) -> None:
        with self._lock:
            self._db.execute(
                "INSERT INTO state (kind, key, value, updated) VALUES (?,?,?,?)"
                " ON CONFLICT(kind, key) DO UPDATE SET value=excluded.value, updated=excluded.updated",
                (kind, str(key), json.dumps(value, ensure_ascii=False),
                 dt.datetime.now().isoformat(timespec="seconds")),
            )
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()
