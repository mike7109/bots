"""Runtime-editable settings, layered over the static config.

The committed config.yaml is the baseline (rules, users, defaults). Anything an
operator flips in the admin panel — kill switch, per-person mute, push mode,
schedule — lives in the SQLite state DB instead, so it changes at runtime
without touching the server or rebuilding the image.

Effective value = DB override if present, else the baseline default. Both the
webhook process (app.py) and the cron process read the same DB on the mounted
volume, so an edit in the UI takes effect on the next webhook/cron tick.
"""
from __future__ import annotations

import datetime as dt

import workcal

_GLOBAL_KEY = "global"
_KIND = "settings"

# push modes for a person's DMs:
#   default — let the message decide (digests ping, i.e. m.text)
#   loud    — always ping (m.text + highlight)
#   quiet   — never push (m.notice; arrives silently)
PUSH_MODES = ("default", "loud", "quiet")
_WEEKDAY_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

# Default per-pass schedule: which weekdays + what time the internal scheduler
# fires each pass. `anchor_days` (digests only) = full overview instead of delta.
WORKDAYS = [0, 1, 2, 3, 4]
PASS_DEFAULTS = {
    "due":     {"enabled": True,  "days": WORKDAYS, "time": "09:00"},
    "overdue": {"enabled": True,  "days": WORKDAYS, "time": "09:00"},
    "digest":  {"enabled": True,  "days": WORKDAYS, "time": "09:00", "anchor_days": [2, 4]},
    "team":    {"enabled": True,  "days": WORKDAYS, "time": "09:30", "anchor_days": [2, 4]},
    "triage":  {"enabled": True,  "days": [0],      "time": "10:00"},
    "stale":   {"enabled": True,  "days": [0],      "time": "10:00"},
    "metrics": {"enabled": False, "days": [0],      "time": "10:00"},
}
HAS_ANCHOR = ("digest", "team")


class Settings:
    def __init__(self, store, *, defaults: dict | None = None):
        self.store = store
        d = defaults or {}
        # Baseline schedule (seeded from env/config at startup); DB overrides win.
        self.defaults = {
            "enabled": True,
            "anchor_days": list(d.get("anchor_days", [2, 4])),
            "weekly_day": d.get("weekly_day", 0),
            "skip_weekends": d.get("skip_weekends", True),
            "holidays": list(d.get("holidays", [])),
            "holidays_auto": d.get("holidays_auto", False),
        }

    # --- global ----------------------------------------------------------
    def _global(self) -> dict:
        return {**self.defaults, **(self.store.get_state(_KIND, _GLOBAL_KEY) or {})}

    def get_global(self) -> dict:
        return self._global()

    def update_global(self, patch: dict) -> dict:
        cur = self.store.get_state(_KIND, _GLOBAL_KEY) or {}
        cur.update(patch)
        self.store.set_state(_KIND, _GLOBAL_KEY, cur)
        return self._global()

    def enabled(self) -> bool:
        return bool(self._global().get("enabled", True))

    def schedule(self) -> dict:
        g = self._global()
        return {
            "anchor_days": frozenset(g["anchor_days"]),
            "weekly_day": g["weekly_day"],
            "skip_weekends": g["skip_weekends"],
            "holidays": frozenset(g["holidays"]),
        }

    # --- per-user prefs --------------------------------------------------
    def _user_key(self, login: str) -> str:
        return f"user:{login}"

    def user_pref(self, login: str) -> dict:
        stored = self.store.get_state(_KIND, self._user_key(login)) or {}
        return {"muted": bool(stored.get("muted", False)),
                "push": stored.get("push", "default")}

    def update_user(self, login: str, patch: dict) -> dict:
        cur = self.store.get_state(_KIND, self._user_key(login)) or {}
        cur.update(patch)
        self.store.set_state(_KIND, self._user_key(login), cur)
        return self.user_pref(login)

    def is_muted(self, login: str) -> bool:
        return self.user_pref(login)["muted"]

    def push_notice(self, login: str, default_notice: bool) -> bool:
        """Resolve the `notice` flag for a person's DM given their push mode."""
        mode = self.user_pref(login)["push"]
        if mode == "quiet":
            return True
        if mode == "loud":
            return False
        return default_notice

    def is_nonworking(self, date_iso: str) -> bool:
        """Is this date silent? Manual holiday list, or (if holidays_auto) a RU
        non-working day per isdayoff.ru."""
        g = self._global()
        if date_iso in (g.get("holidays") or []):
            return True
        if g.get("holidays_auto") and workcal.is_day_off(date_iso, store=self.store):
            return True
        return False

    # --- per-pass schedule (days + time the scheduler fires each pass) ----
    def pass_schedule(self, name: str) -> dict:
        base = dict(PASS_DEFAULTS.get(name, {"enabled": True, "days": WORKDAYS, "time": "09:00"}))
        base.update(self.store.get_state(_KIND, f"pass:{name}") or {})
        return base

    def all_pass_schedules(self) -> dict:
        return {name: self.pass_schedule(name) for name in PASS_DEFAULTS}

    def update_pass(self, name: str, patch: dict) -> dict:
        cur = self.store.get_state(_KIND, f"pass:{name}") or {}
        cur.update(patch)
        self.store.set_state(_KIND, f"pass:{name}", cur)
        return self.pass_schedule(name)

    # --- rule overrides (enable/disable + destination) -------------------
    def rule_override(self, event: str) -> dict | None:
        return self.store.get_state(_KIND, f"rule:{event}")

    def update_rule(self, event: str, patch: dict) -> dict:
        cur = self.store.get_state(_KIND, f"rule:{event}") or {}
        cur.update(patch)
        self.store.set_state(_KIND, f"rule:{event}", cur)
        return cur


def weekday_names(days) -> str:
    return ", ".join(_WEEKDAY_NAMES[d] for d in sorted(days)) or "—"
