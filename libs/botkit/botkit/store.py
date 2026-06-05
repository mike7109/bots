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
        # WAL lets a reader and the writer proceed concurrently (host-cron and the
        # app may touch the same file); busy_timeout is the explicit form of the
        # default — wait up to 5s for a lock instead of erroring immediately.
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA busy_timeout=5000")
        # Schema version marker — placeholder for future migrations (set if unset).
        if self._db.execute("PRAGMA user_version").fetchone()[0] == 0:
            self._db.execute("PRAGMA user_version=1")
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
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS log ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " ts TEXT NOT NULL, day TEXT NOT NULL,"
            " kind TEXT, action TEXT, channel TEXT,"
            " status TEXT NOT NULL,"      # sent | skipped | error | ignored
            " detail TEXT)"
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

    # --- last-run history (per-pass) — reuses the `state` table, kind="run" ---
    def record_run(self, key: str, iso: str, *, ok: bool = True, detail: str = "") -> None:
        """Remember the last run of a pass (its scheduled date + outcome), so the
        scheduler can tell a genuine missed run (recover) from a fresh deploy."""
        self.set_state("run", key, {
            "iso": iso, "ok": ok,
            "ts": dt.datetime.now().isoformat(timespec="seconds"),
            "detail": detail[:300],
        })

    def last_run(self, key: str) -> dict | None:
        """The last recorded run for a pass key, or None if it never ran."""
        return self.get_state("run", key)

    def clear_state(self, kind: str, key: str | int) -> None:
        with self._lock:
            self._db.execute("DELETE FROM state WHERE kind=? AND key=?", (kind, str(key)))
            self._db.commit()

    def list_state(self, kind: str) -> dict:
        """All (key -> value) rows for a kind — e.g. every configured source."""
        with self._lock:
            rows = self._db.execute("SELECT key, value FROM state WHERE kind=?", (kind,)).fetchall()
        return {k: json.loads(v) for k, v in rows}

    # --- activity log + stats -------------------------------------------
    def log_event(self, kind, action, channel, status: str, detail: str = "") -> None:
        now = dt.datetime.now()
        with self._lock:
            self._db.execute(
                "INSERT INTO log (ts, day, kind, action, channel, status, detail)"
                " VALUES (?,?,?,?,?,?,?)",
                (now.isoformat(timespec="seconds"), now.date().isoformat(),
                 kind, action, channel, status, detail[:500]),
            )
            self._db.commit()

    def recent_log(self, limit: int = 100, status: str | None = None) -> list[dict]:
        q = "SELECT ts, kind, action, channel, status, detail FROM log"
        args: tuple = ()
        if status:
            q += " WHERE status=?"
            args = (status,)
        q += " ORDER BY id DESC LIMIT ?"
        with self._lock:
            rows = self._db.execute(q, (*args, limit)).fetchall()
        cols = ("ts", "kind", "action", "channel", "status", "detail")
        return [dict(zip(cols, r)) for r in rows]

    def log_stats(self, days: int = 7) -> dict:
        today = dt.date.today().isoformat()
        since = (dt.date.today() - dt.timedelta(days=days - 1)).isoformat()
        with self._lock:
            by_status_today = dict(self._db.execute(
                "SELECT status, count(*) FROM log WHERE day=? GROUP BY status", (today,)).fetchall())
            by_status_week = dict(self._db.execute(
                "SELECT status, count(*) FROM log WHERE day>=? GROUP BY status", (since,)).fetchall())
            by_kind = dict(self._db.execute(
                "SELECT kind, count(*) FROM log WHERE day>=? AND status='sent' GROUP BY kind ORDER BY 2 DESC",
                (since,)).fetchall())
        return {"today": by_status_today, "week": by_status_week, "by_kind": by_kind, "days": days}

    def close(self) -> None:
        with self._lock:
            self._db.close()
