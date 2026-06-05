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
import logging

from botkit.config import env

import passes
import workcal

log = logging.getLogger("gitlab-notify.settings")

# Connection settings the admin can override (DB wins over the env default).
CONN_FIELDS = {
    "matrix_homeserver": "MATRIX_HOMESERVER",
    "matrix_token": "MATRIX_TOKEN",
    "webhook_secret": "WEBHOOK_SECRET",
    "gitlab_url": "GITLAB_URL",
}

_GLOBAL_KEY = "global"
_KIND = "settings"

# Global active-hours window (time-of-day): outside it scheduled mailings are
# silent. Complements the per-DAY non-working calendar (is_nonworking). Defaults:
# enabled, 08:00–20:00 (container TZ), webhooks stay realtime (quiet=off). NB:
# every current pass fires 09:00–10:00 — inside 08:00–20:00 — so enabling this by
# default leaves existing scheduled passes unaffected.
ACTIVE_HOURS_DEFAULTS = {
    "enabled": True,
    "from": "08:00",
    "until": "20:00",
    "webhooks_quiet": False,
}


def _parse_hhmm(s: str) -> int | None:
    """Minutes-of-day for an "HH:MM" string, or None if malformed."""
    if not isinstance(s, str) or len(s) != 5 or s[2] != ":":
        return None
    try:
        h, m = int(s[:2]), int(s[3:])
    except ValueError:
        return None
    if not (0 <= h <= 23 and 0 <= m <= 59):
        return None
    return h * 60 + m

# push modes for a person's DMs:
#   default — let the message decide (digests ping, i.e. m.text)
#   loud    — always ping (m.text + highlight)
#   quiet   — never push (m.notice; arrives silently)
PUSH_MODES = ("default", "loud", "quiet")
_WEEKDAY_NAMES = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

# Default per-pass schedule: which weekdays + what time the internal scheduler
# fires each pass. `anchor_days` (digests only) = full overview instead of delta.
# DERIVED from the single pass registry (passes.py) so adding a pass is one entry
# there; same names/values as before.
WORKDAYS = [0, 1, 2, 3, 4]
PASS_DEFAULTS = passes.pass_defaults()
HAS_ANCHOR = passes.has_anchor()


class Settings:
    def __init__(self, store, *, defaults: dict | None = None):
        self.store = store
        d = defaults or {}
        # Baseline defaults; DB overrides (admin panel) win.
        self.defaults = {
            "enabled": True,            # global kill switch (stops everything)
            "scheduler_on": True,       # auto-digests on/off (webhooks still fire)
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

    # --- active-hours window (time-of-day the bot is allowed to act) ------
    def get_active_hours(self) -> dict:
        stored = self.store.get_state(_KIND, "active_hours") or {}
        out = dict(ACTIVE_HOURS_DEFAULTS)
        if isinstance(stored.get("enabled"), bool):
            out["enabled"] = stored["enabled"]
        if isinstance(stored.get("webhooks_quiet"), bool):
            out["webhooks_quiet"] = stored["webhooks_quiet"]
        for k in ("from", "until"):
            if _parse_hhmm(stored.get(k)) is not None:
                out[k] = stored[k]
        return out

    def update_active_hours(self, patch: dict) -> dict:
        cur = self.store.get_state(_KIND, "active_hours") or {}
        if "enabled" in patch:
            cur["enabled"] = bool(patch["enabled"])
        if "webhooks_quiet" in patch:
            cur["webhooks_quiet"] = bool(patch["webhooks_quiet"])
        for k in ("from", "until"):
            if k in patch and _parse_hhmm(patch[k]) is not None:
                cur[k] = patch[k]                     # only persist valid HH:MM
        self.store.set_state(_KIND, "active_hours", cur)
        return self.get_active_hours()

    def is_active_now(self, now: dt.datetime | None = None) -> bool:
        """Is the current time inside the active-hours window?

        Disabled -> always active. A normal daytime window (from <= until) is
        active iff from_min <= now_min <= until_min. A reversed window
        (from > until) is unsupported in v1: treat as always-active and warn
        (don't silently brick all mailings). Malformed HH:MM -> fail OPEN (active).

        Reads the RAW stored config (not the sanitized getter) so a corrupt
        DB row genuinely hits the fail-open path rather than being masked by a
        silent default."""
        stored = self.store.get_state(_KIND, "active_hours") or {}
        enabled = stored.get("enabled", ACTIVE_HOURS_DEFAULTS["enabled"])
        if not enabled:
            return True
        from_min = _parse_hhmm(stored.get("from", ACTIVE_HOURS_DEFAULTS["from"]))
        until_min = _parse_hhmm(stored.get("until", ACTIVE_HOURS_DEFAULTS["until"]))
        if from_min is None or until_min is None:     # bad config -> don't brick
            log.warning("active-hours: bad HH:MM (from=%r until=%r) -> treating as active",
                        stored.get("from"), stored.get("until"))
            return True
        if from_min > until_min:                      # reversed window unsupported in v1
            log.warning("active-hours: from (%s) > until (%s) unsupported in v1 -> active",
                        stored.get("from"), stored.get("until"))
            return True
        now = now or dt.datetime.now()
        now_min = now.hour * 60 + now.minute
        return from_min <= now_min <= until_min

    # --- connection settings (admin panel is the single source of truth) -----
    def seed_conn(self) -> None:
        """One-time migration: if the DB has no connection settings yet, take
        them from env. After that the DB (admin panel) is authoritative and env
        is ignored — no env/admin duality."""
        if self.store.get_state("settings", "conn") is not None:
            return
        self.store.set_state("settings", "conn",
                             {f: env(ev) for f, ev in CONN_FIELDS.items() if env(ev)})

    def get_conn(self) -> dict:
        stored = self.store.get_state("settings", "conn") or {}
        return {f: stored.get(f, "") for f in CONN_FIELDS}

    def conn_value(self, field: str) -> str:
        return self.get_conn().get(field, "")

    def update_conn(self, patch: dict) -> dict:
        cur = self.store.get_state("settings", "conn") or {}
        for f in CONN_FIELDS:
            if f in patch:
                if patch[f]:
                    cur[f] = patch[f]
                else:
                    cur.pop(f, None)         # cleared -> fall back to env
        self.store.set_state("settings", "conn", cur)
        return self.get_conn()

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

    # --- operator alerts (who gets DM'd when the bot itself fails) --------
    def get_alerts(self) -> dict:
        """Who receives DM alerts about the bot's OWN failures, and whether
        alerting is on. `engineers` are GitLab logins (resolved to mxids by the
        Alerter). Defaults: nobody listed, but enabled (so it works the moment
        the owner adds themselves)."""
        stored = self.store.get_state(_KIND, "alerts") or {}
        engineers = stored.get("engineers")
        return {
            "engineers": list(engineers) if isinstance(engineers, list) else [],
            "enabled": bool(stored.get("enabled", True)),
        }

    def update_alerts(self, patch: dict) -> dict:
        cur = self.store.get_state(_KIND, "alerts") or {}
        if "engineers" in patch:
            eng = patch["engineers"]
            if isinstance(eng, list):
                cur["engineers"] = [str(x) for x in eng]
        if "enabled" in patch:
            cur["enabled"] = bool(patch["enabled"])
        self.store.set_state(_KIND, "alerts", cur)
        return self.get_alerts()

    # --- send guard (rate-limit + circuit breaker) -----------------------
    # Thresholds for botkit's SendGuard. Defaults sit comfortably ABOVE any
    # legitimate burst so normal operation never trips: a full personal digest
    # DMs every assignee (~11 people) plus the team room in the same minute, so
    # max_global=50/10min and max_per_target=5/5min clear that with headroom,
    # while still catching a runaway loop (hundreds of sends, or one message
    # repeated). auto_reset_s=0 -> manual reset only (an operator must look).
    GUARD_DEFAULTS = {
        "enabled": True,
        "window_s": 600,          # global lookback (10 min)
        "max_global": 50,         # > a full digest burst (≈11 DMs + team msg)
        "target_window_s": 300,   # per-recipient lookback (5 min)
        "max_per_target": 5,      # one person shouldn't get >5 in 5 min normally
        "max_duplicate": 3,       # the same exact message >3× means a loop
        "auto_reset_s": 0,        # 0 = manual reset only
    }

    def get_guard(self) -> dict:
        stored = self.store.get_state(_KIND, "guard") or {}
        out = {}
        for k, default in self.GUARD_DEFAULTS.items():
            v = stored.get(k, default)
            out[k] = bool(v) if isinstance(default, bool) else int(v)
        return out

    def update_guard(self, patch: dict) -> dict:
        cur = self.store.get_state(_KIND, "guard") or {}
        for k, default in self.GUARD_DEFAULTS.items():
            if k not in patch:
                continue
            v = patch[k]
            if isinstance(default, bool):
                cur[k] = bool(v)
            else:
                try:
                    cur[k] = max(0, int(v))      # counts/seconds are non-negative
                except (ValueError, TypeError):
                    pass                          # ignore garbage, keep prior/default
        self.store.set_state(_KIND, "guard", cur)
        return self.get_guard()

    # --- breaker trip STATE (persistent — survives restart) --------------
    # Critical: if a crash-loop keeps restarting the process, the tripped flag
    # must persist so it doesn't quietly resume spamming on boot.
    def get_breaker(self) -> dict:
        stored = self.store.get_state(_KIND, "breaker") or {}
        return {
            "tripped": bool(stored.get("tripped", False)),
            "since": stored.get("since"),
            "reason": str(stored.get("reason", "")),
        }

    def set_breaker(self, state: dict) -> dict:
        tripped = bool(state.get("tripped", False))
        if tripped:
            self.store.set_state(_KIND, "breaker", {
                "tripped": True,
                "since": state.get("since"),
                "reason": str(state.get("reason", "")),
            })
        else:
            self.store.set_state(_KIND, "breaker", {"tripped": False})
        return self.get_breaker()

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
