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

import keys
import passes
import workcal

log = logging.getLogger("gitlab-notify.settings")

# Connection settings the admin can override (DB wins over the env default).
CONN_FIELDS = {
    "matrix_homeserver": "MATRIX_HOMESERVER",
    "matrix_token": "MATRIX_TOKEN",
    "webhook_secret": "WEBHOOK_SECRET",
    "gitlab_url": "GITLAB_URL",
    # Shared secret the issue-graph service sends as `Authorization: Bearer <…>`
    # to the public POST /api/poke endpoint. Fail-closed: empty -> poke disabled.
    "poke_token": "POKE_TOKEN",
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
            # Overlap guard (Phase 3a): after a *_full digest SENDS, suppress the
            # intraday *_delta for this many hours so it doesn't restate what the
            # full just showed (full reads, delta still owns the baseline). 0=off.
            "delta_quiet_after_full_h": d.get("delta_quiet_after_full_h", 3),
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
        """Effective schedule for a pass: registry defaults + admin overrides.

        The scheduler branches on `kind`: "interval" (the delta digests — fired
        every ~`every_hours` within the active window, or early on a burst of
        `change_threshold` changes, throttled by `floor_min`) vs "daily"/calendar
        (everything else). A missing/old `kind` defaults to "daily" so passes that
        never carried the field — and any pre-Phase-3a override row — stay
        calendar-scheduled with their existing days/time. `enabled`/`days`/`time`
        plus any extra fields (days_idle, the interval fields) merge defaults
        under admin overrides.
        """
        base = dict(PASS_DEFAULTS.get(name, {"enabled": True, "days": WORKDAYS, "time": "09:00"}))
        base.update(self.store.get_state(_KIND, f"pass:{name}") or {})
        base.setdefault("kind", "daily")              # tolerate old/missing kind
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

    # --- poke anti-spam (the issue-graph "пнуть исполнителя" endpoint) ----
    # Soft limits applied BEFORE sending (no guard.record on rejection), so a
    # poke flood is throttled here without tripping the global breaker. Kept
    # comfortably BELOW the guard's per-target cap (max_per_target=5/5min) so a
    # legitimate poke can never be the message that trips the breaker:
    #   per_user_max=4 per 600s window < max_per_target=5 per 300s.
    POKE_DEFAULTS = {
        "cooldown_s": 600,          # per (user, issue): don't re-poke within 10 min
        "per_user_window_s": 600,   # rolling window for the per-user rate cap
        "per_user_max": 4,          # max pokes to one person per window (< breaker)
    }

    def get_poke(self) -> dict:
        stored = self.store.get_state(_KIND, "poke_cfg") or {}
        out = {}
        for k, default in self.POKE_DEFAULTS.items():
            try:
                out[k] = max(0, int(stored.get(k, default)))
            except (ValueError, TypeError):
                out[k] = default
        return out

    def update_poke(self, patch: dict) -> dict:
        cur = self.store.get_state(_KIND, "poke_cfg") or {}
        for k in self.POKE_DEFAULTS:
            if k not in patch:
                continue
            try:
                cur[k] = max(0, int(patch[k]))       # counts/seconds are non-negative
            except (ValueError, TypeError):
                pass                                  # ignore garbage, keep prior/default
        self.store.set_state(_KIND, "poke_cfg", cur)
        return self.get_poke()

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

    # --- label display whitelist (which 🏷 chips appear in messages) ------
    # When enabled with a non-empty allow-list, only labels in the list are
    # shown on issue messages and digest items (templates call display_labels()).
    # Disabled OR an empty list -> show everything (the prior behaviour), so the
    # feature is opt-in and can never accidentally hide all labels.
    def get_label_display(self) -> dict:
        stored = self.store.get_state(_KIND, "label_display") or {}
        allow = stored.get("allow")
        return {
            "enabled": bool(stored.get("enabled", False)),
            "allow": [str(x) for x in allow] if isinstance(allow, list) else [],
        }

    def update_label_display(self, patch: dict) -> dict:
        cur = self.store.get_state(_KIND, "label_display") or {}
        if "enabled" in patch:
            cur["enabled"] = bool(patch["enabled"])
        if isinstance(patch.get("allow"), list):
            seen, out = set(), []                     # trim, drop blanks, dedupe (keep order)
            for x in patch["allow"]:
                s = str(x).strip()
                if s and s not in seen:
                    seen.add(s)
                    out.append(s)
            cur["allow"] = out
        self.store.set_state(_KIND, "label_display", cur)
        return self.get_label_display()

    def display_labels(self, labels) -> list:
        """Filter a label list for display by the admin whitelist. Disabled or
        empty allow-list -> unchanged (show all). Used as a Jinja global so both
        issue messages and digest items honour the same policy."""
        cfg = self.get_label_display()
        if not cfg["enabled"] or not cfg["allow"]:
            return list(labels or [])
        allow = set(cfg["allow"])
        return [lbl for lbl in (labels or []) if lbl in allow]

    # --- watched issues (special tags -> extra group rooms) ---------------
    # A list of rules {id, name, tags[], rooms[], enabled}. An issue carrying ANY
    # of a rule's tags is "special": the webhook also posts it to that rule's
    # rooms (independent of the project's source room — works even with no source
    # match). Stored as one JSON list under key "watched".
    def get_watched(self) -> list:
        stored = self.store.get_state(_KIND, "watched")
        rules = stored.get("rules") if isinstance(stored, dict) else None
        out = []
        for r in (rules or []):
            if not isinstance(r, dict):
                continue
            out.append({
                "id": str(r.get("id") or ""),
                "name": str(r.get("name") or ""),
                "tags": [str(t) for t in (r.get("tags") or []) if str(t).strip()],
                "rooms": [str(rm).strip() for rm in (r.get("rooms") or []) if str(rm).strip()],
                "enabled": bool(r.get("enabled", True)),
            })
        return out

    def set_watched(self, rules) -> list:
        clean, seen_ids = [], set()
        for i, r in enumerate(rules if isinstance(rules, list) else []):
            if not isinstance(r, dict):
                continue
            rid = str(r.get("id") or "").strip() or f"w{i + 1}"
            while rid in seen_ids:                     # guarantee unique ids
                rid += "_"
            seen_ids.add(rid)
            tags, rooms = [], []
            for t in (r.get("tags") or []):
                s = str(t).strip()
                if s and s not in tags:
                    tags.append(s)
            for rm in (r.get("rooms") or []):
                s = str(rm).strip()
                if s and s not in rooms:
                    rooms.append(s)
            clean.append({
                "id": rid,
                "name": str(r.get("name") or "").strip(),
                "tags": tags,
                "rooms": rooms,
                "enabled": bool(r.get("enabled", True)),
            })
        self.store.set_state(_KIND, "watched", {"rules": clean})
        return self.get_watched()

    def watched_targets(self, labels) -> list:
        """Enabled watched rules whose tags intersect this issue's labels (and
        that have at least one destination room). Each result carries its rooms."""
        have = set(labels or [])
        return [r for r in self.get_watched()
                if r["enabled"] and r["tags"] and r["rooms"] and have.intersection(r["tags"])]


def weekday_names(days) -> str:
    return ", ".join(_WEEKDAY_NAMES[d] for d in sorted(days)) or "—"


# --- Phase 2a: split the anchor-dual digests into separate full + delta passes -
# Old world: one `digest`/`team` pass each ran a FULL overview on its anchor
# weekdays (default Wed/Fri) and a DELTA on the other workdays. New world: those
# are two passes apiece — digest_full/digest_delta, team_full/team_delta — each
# with its own schedule. This one-time, idempotent migration carries the old
# schedule row over so a live deploy keeps its tuned days/time/enabled and so the
# scheduler treats the new passes as ESTABLISHED (not a fresh deploy that would
# refuse to recover a missed slot).
_SPLIT = {
    # old name -> (full name, delta name, default full days, default delta days, default time)
    "digest": ("digest_full", "digest_delta", [2, 4], [0, 1, 3], "09:00"),
    "team": ("team_full", "team_delta", [2, 4], [0, 1, 3], "09:30"),
}
_SPLIT_MARKER = ("migration", "split_digests_v1")


def migrate_split_digests(store, *, today: dt.date | None = None) -> bool:
    """Carry old `pass:digest`/`pass:team` rows onto the four split passes.

    Idempotent + marker-guarded: returns False (no-op) once `split_digests_v1`
    is set. For each old pass row we create the full pass (days = old
    `anchor_days` or the default full days; time/enabled from the old row) and the
    delta pass (days = old `days` minus its anchor_days, or the default delta
    days; same time/enabled). The old rows are LEFT in place as a rollback escape
    hatch. We then SEED run-history for the new pass names (dated yesterday) for
    every configured source so the scheduler's missed-run recovery treats them as
    established rather than blasting on first tick. The delta baseline needs no
    migration: event_kind is unchanged, so the existing digest_personal/
    digest_team snapshots already serve as the delta's baseline under the same key.
    """
    if store.get_state(*_SPLIT_MARKER):
        return False
    today = today or dt.date.today()
    yesterday = (today - dt.timedelta(days=1)).isoformat()
    # Source ids: the scheduler keys run-history as keys.ns(src_id, pass_name);
    # sources live in the state DB under kind "source". Best-effort — if none are
    # configured yet, seed under skey="" so a single-source deploy still recovers.
    src_ids = list(store.list_state("source").keys()) or [""]
    new_enabled: list[str] = []

    for old_name, (full_name, delta_name, def_full, def_delta, def_time) in _SPLIT.items():
        old = store.get_state(_KIND, f"pass:{old_name}") or {}
        enabled = bool(old.get("enabled", True))
        time = old.get("time", def_time)
        anchor_days = sorted(int(d) for d in old.get("anchor_days", def_full))
        all_days = sorted(int(d) for d in old.get("days", [])) if old.get("days") else None
        # Full pass runs on the old anchor days; delta on the remaining workdays.
        full_days = anchor_days or list(def_full)
        if all_days is not None:
            delta_days = [d for d in all_days if d not in set(anchor_days)] or list(def_delta)
        else:
            delta_days = list(def_delta)
        # Upsert (idempotent): only sets the schedule fields the split owns.
        store.set_state(_KIND, f"pass:{full_name}",
                        {"enabled": enabled, "days": full_days, "time": time})
        store.set_state(_KIND, f"pass:{delta_name}",
                        {"enabled": enabled, "days": delta_days, "time": time})
        if enabled:
            new_enabled += [full_name, delta_name]

    # Seed run-history (yesterday) so the new passes look ESTABLISHED, not fresh.
    for sid in src_ids:
        for name in new_enabled:
            schedkey = keys.ns(sid, name)
            if store.last_run(schedkey) is None:        # don't clobber a real run
                store.record_run(schedkey, yesterday, ok=True)

    store.set_state(*_SPLIT_MARKER, True)
    log.info("split-digests migration: created %s; seeded run-history for %d source(s)",
             ", ".join(sorted({n for v in _SPLIT.values() for n in v[:2]})), len(src_ids))
    return True
