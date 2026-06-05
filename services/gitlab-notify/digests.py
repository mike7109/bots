"""Digest & hygiene cron passes: roll many issues into one message.

A webhook event is one issue -> one notification. A digest is the opposite: a
whole list folded into a single Matrix message. It still flows through the same
engine — each pass builds an Event whose `extra.sections` carries the grouped
issues, and a matching template renders them — so routing, dedup and transports
stay identical to everything else.

Passes (each is a cron subcommand, see cron.py):
  personal  per-assignee DM "what's on me", issues grouped by deadline
  team      shared room standup overview (overdue / today / soon / in progress)
  triage    shared room: issues needing triage (no assignee / no due date)
  stale     shared room: open issues with no activity for N days
  metrics   shared room: weekly issue flow (throughput / WIP / age / cycle p85)

Each pass dedups through botkit.store so a second run the same day is a no-op.
Empty digests are skipped — silence beats a "nothing to report" ping.
"""
from __future__ import annotations

import datetime as dt
import logging
import math

from botkit.notify.event import Event

log = logging.getLogger("gitlab-notify.digest")

WORKFLOW_PREFIX = "workflow::"          # board-column scoped labels
IN_PROGRESS = "workflow::in progress"   # the team's agreed "started" label
TEAM_SOON_DAYS = 3                      # team digest "near deadlines" window
PERSONAL_SOON_DAYS = 7                  # personal digest "this week" window
STALE_DAYS = 14                         # no activity for this long -> stale
METRICS_WINDOW = 7                      # metrics look-back, days

# Personal digest is delta-driven: it only writes when something actually
# changed, so people aren't pinged with the same list every morning. On
# "anchor" weekdays it sends a full overview even with no changes (defaults to
# Wed+Fri — issues are usually handed out Mon, so a midweek/end-of-week recap
# helps). Weekends and listed holidays are fully silent.
ANCHOR_DAYS = frozenset({2, 4})         # Wed, Fri (0=Mon .. 6=Sun)
WEEKLY_DAY = 0                          # triage/stale hygiene: Monday morning
_WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def parse_day(spec: str, default: int = 0) -> int:
    """Single weekday: 'mon' -> 0. Falls back to `default` on a bad token."""
    return _WEEKDAYS.get(spec.strip().lower()[:3], default)


def parse_days(spec: str) -> frozenset[int]:
    """'wed,fri' -> {2, 4}. Unknown tokens are ignored."""
    return frozenset(
        _WEEKDAYS[t.strip().lower()[:3]] for t in spec.split(",")
        if t.strip().lower()[:3] in _WEEKDAYS
    )


# --- shared helpers ------------------------------------------------------
def _today() -> dt.date:
    return dt.date.today()


def _date(s: str) -> dt.date:
    return dt.date.fromisoformat(str(s)[:10])


def _column(labels) -> str | None:
    """The board column an issue sits in = its `workflow::` scoped label.

    On Free these aren't mutually exclusive (that's Premium), so an issue can
    carry several — we take the first. None means it's in the default Open
    list (no workflow label yet).
    """
    for lbl in labels or []:
        if lbl.startswith(WORKFLOW_PREFIX):
            return lbl[len(WORKFLOW_PREFIX):]
    return None


def _row(issue: dict) -> dict:
    """The per-issue shape templates iterate over."""
    return {
        "iid": issue.get("iid"),
        "title": issue.get("title", ""),
        "url": issue.get("web_url", ""),
        "due": issue.get("due_date"),
        "labels": issue.get("labels", []),
        "col": _column(issue.get("labels", [])),
        "assignees": [a.get("username") for a in (issue.get("assignees") or []) if a.get("username")],
    }


def _bucket(due: str, today_iso: str, horizon_iso: str) -> str:
    if not due:
        return "none"
    if due < today_iso:
        return "overdue"
    if due == today_iso:
        return "today"
    if due <= horizon_iso:
        return "soon"
    return "later"


def _snapshot(rows: list[dict], today_iso: str, horizon_iso: str) -> dict:
    """Per-issue state we compare run-to-run to detect what changed."""
    return {
        str(r["iid"]): {
            "iid": r["iid"], "title": r["title"], "url": r["url"],
            "due": r["due"] or "", "col": r["col"], "assignees": r["assignees"],
            "bucket": _bucket(r["due"], today_iso, horizon_iso),
        }
        for r in rows
    }


def _diff(prev: dict, cur: dict) -> dict:
    """Categorise what changed between two snapshots of one person's issues."""
    out = {"new": [], "removed": [], "moved": [], "due": [], "overdue": [], "today": []}
    pids, cids = set(prev), set(cur)
    for i in cids - pids:
        out["new"].append(cur[i])
    for i in pids - cids:
        out["removed"].append(prev[i])
    for i in cids & pids:
        p, c = prev[i], cur[i]
        if c["col"] != p["col"]:
            out["moved"].append({**c, "from": p["col"]})
        if c["due"] != p["due"]:
            out["due"].append({**c, "from_due": p["due"]})
        elif c["bucket"] != p["bucket"]:
            # deadline didn't move, but time passed: did it cross into overdue/today?
            if c["bucket"] == "overdue":
                out["overdue"].append(c)
            elif c["bucket"] == "today":
                out["today"].append(c)
    return out


def _by_due(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: r["due"] or "")


def _ns(skey: str, key) -> str:
    """Namespace a dedup/snapshot key by source so groups don't collide."""
    return f"{skey}:{key}" if skey else str(key)


def _emit(engine, store, kind: str, dedup_key: str, extra: dict, assignees=None,
          *, day=None, room=None) -> int:
    """Build the digest Event, dispatch it, and mark it sent on success."""
    if store.already_sent(kind, dedup_key, day=day):
        log.info("%s [%s]: already sent today", kind, dedup_key)
        return 0
    event = Event(kind=kind, action="digest", assignees=assignees or [], extra=extra, room=room)
    result = engine.handle(event)
    if result.get("sent"):
        store.mark_sent(kind, dedup_key, day=day)
        return 1
    log.warning("%s [%s] not delivered: %s", kind, dedup_key, result)
    return 0


# --- passes --------------------------------------------------------------
def _full_sections(rows: list[dict], today: str, horizon: str) -> list[dict]:
    """The grouped-by-deadline view used for the anchor-day overview."""
    overdue = [r for r in rows if r["due"] and r["due"] < today]
    due_today = [r for r in rows if r["due"] == today]
    soon = [r for r in rows if r["due"] and today < r["due"] <= horizon]
    later = [r for r in rows if r["due"] and r["due"] > horizon]
    no_date = [r for r in rows if not r["due"]]
    sections = []
    if overdue:
        sections.append({"emoji": "⏰", "title": "Просрочено", "items": _by_due(overdue)})
    if due_today:
        sections.append({"emoji": "📅", "title": "Сегодня", "items": due_today})
    if soon:
        sections.append({"emoji": "🔜", "title": "На этой неделе", "items": _by_due(soon)})
    if later:
        sections.append({"emoji": "🗓", "title": "Позже", "items": _by_due(later)})
    if no_date:
        sections.append({"emoji": "📋", "title": "Без срока", "items": no_date})
    return sections


def personal(engine, issues: list[dict], store, *, today: dt.date | None = None,
             anchor_days=ANCHOR_DAYS, holidays=frozenset(), skip_weekends: bool = True,
             room=None, skey: str = "") -> int:
    """Per-assignee DM, delta-driven.

    Only writes when a person's issues actually changed (new / moved column /
    due changed / crossed into overdue-or-today / removed) — no daily repeat of
    an unchanged list. On `anchor_days` it sends a full overview regardless.
    Weekends (unless skip_weekends=False) and `holidays` (ISO dates) are silent.
    """
    today = today or _today()
    iso = today.isoformat()
    if (skip_weekends and today.weekday() >= 5) or iso in holidays:
        log.info("personal digest: quiet day (%s) — silent", iso)
        return 0
    is_anchor = today.weekday() in anchor_days
    horizon = (today + dt.timedelta(days=PERSONAL_SOON_DAYS)).isoformat()

    by_user: dict[str, list[dict]] = {}
    for issue in issues:
        for a in (issue.get("assignees") or []):
            login = a.get("username")
            if login:
                by_user.setdefault(login, []).append(issue)

    sent = 0
    for login, items in sorted(by_user.items()):
        pkey = _ns(skey, login)
        if store.already_sent("digest_personal", pkey, day=iso):   # one DM per person per day
            continue
        rows = [_row(i) for i in items]
        current = _snapshot(rows, iso, horizon)
        prev = store.get_state("digest_personal", pkey)

        if is_anchor:
            extra = {"mode": "full", "date": iso, "total": len(rows),
                     "sections": _full_sections(rows, iso, horizon)}
        elif prev is None:
            # First time we see this person off an anchor day: learn their
            # baseline silently, so the next change is a real delta, not "all new".
            store.set_state("digest_personal", pkey, current)
            continue
        else:
            changes = _diff(prev, current)
            if not any(changes.values()):
                continue                                    # nothing changed -> silent
            extra = {"mode": "delta", "date": iso, "changes": changes}

        event = Event(kind="digest_personal", action="digest", assignees=[login], extra=extra, room=room)
        result = engine.handle(event)
        if result.get("sent"):
            store.mark_sent("digest_personal", pkey, day=iso)
            store.set_state("digest_personal", pkey, current)
            sent += 1
        else:
            log.warning("digest_personal [%s] not delivered: %s", login, result)
    log.info("personal digest: %d sent (%s)", sent, "anchor" if is_anchor else "delta")
    return sent


def _team_sections(rows: list[dict], today: str, horizon: str) -> list[dict]:
    """Standup overview, each issue in exactly one section, most-urgent-first."""
    overdue, due_today, soon, in_progress = [], [], [], []
    for r in rows:
        if r["due"] and r["due"] < today:
            overdue.append(r)
        elif r["due"] == today:
            due_today.append(r)
        elif r["due"] and r["due"] <= horizon:
            soon.append(r)
        elif IN_PROGRESS in (r["labels"] or []):
            in_progress.append(r)
    sections = []
    if overdue:
        sections.append({"emoji": "⏰", "title": "Просрочено", "items": _by_due(overdue), "show_who": True})
    if due_today:
        sections.append({"emoji": "📅", "title": "Дедлайн сегодня", "items": due_today, "show_who": True})
    if soon:
        sections.append({"emoji": "🔜", "title": "Ближайшие дедлайны", "items": _by_due(soon), "show_who": True})
    if in_progress:
        sections.append({"emoji": "🚧", "title": "В работе", "items": in_progress, "show_who": True})
    return sections


def team(engine, issues: list[dict], store, *, today: dt.date | None = None,
         anchor_days=ANCHOR_DAYS, holidays=frozenset(), skip_weekends: bool = True,
         room=None, skey: str = "") -> int:
    """Shared room standup digest, delta-driven (same rules as `personal`).

    Writes only when the group's issues changed, except on `anchor_days` where
    it posts a full standup overview. Weekends/holidays are silent.
    """
    today = today or _today()
    iso = today.isoformat()
    if (skip_weekends and today.weekday() >= 5) or iso in holidays:
        log.info("team digest: quiet day (%s) — silent", iso)
        return 0
    is_anchor = today.weekday() in anchor_days
    sec_horizon = (today + dt.timedelta(days=TEAM_SOON_DAYS)).isoformat()
    snap_horizon = (today + dt.timedelta(days=PERSONAL_SOON_DAYS)).isoformat()

    gkey = _ns(skey, "group")
    rows = [_row(i) for i in issues]
    current = _snapshot(rows, iso, snap_horizon)
    prev = store.get_state("digest_team", gkey)

    if store.already_sent("digest_team", gkey, day=iso):
        return 0
    if is_anchor:
        sections = _team_sections(rows, iso, sec_horizon)
        if not sections:
            return 0
        extra = {"mode": "full", "date": iso, "open_total": len(rows), "sections": sections}
    elif prev is None:
        store.set_state("digest_team", gkey, current)   # learn baseline silently
        return 0
    else:
        changes = _diff(prev, current)
        if not any(changes.values()):
            return 0
        extra = {"mode": "delta", "date": iso, "changes": changes}

    event = Event(kind="digest_team", action="digest", extra=extra, room=room)
    result = engine.handle(event)
    if result.get("sent"):
        store.mark_sent("digest_team", gkey, day=iso)
        store.set_state("digest_team", gkey, current)
        return 1
    log.warning("digest_team not delivered: %s", result)
    return 0


def _weekly_skip(today: dt.date | None, weekly_day: int, holidays) -> tuple[dt.date, bool]:
    """Hygiene digests run once a week. Return (today, should_skip)."""
    today = today or _today()
    skip = today.weekday() != weekly_day or today.isoformat() in holidays
    return today, skip


def triage(engine, issues: list[dict], store, *, today: dt.date | None = None,
           weekly_day: int = WEEKLY_DAY, holidays=frozenset(), room=None, skey: str = "") -> int:
    """Shared room, weekly: issues that need a decision (unassigned / no deadline).

    A full overview (not a delta) — once a week you want the whole hygiene
    picture, not just what changed. Runs only on `weekly_day` (default Monday).
    """
    today, skip = _weekly_skip(today, weekly_day, holidays)
    if skip:
        log.info("triage digest: not the weekly day (%s) — skip", today.isoformat())
        return 0

    no_assignee = [_row(i) for i in issues if not (i.get("assignees"))]
    no_due = [_row(i) for i in issues if (i.get("assignees")) and not i.get("due_date")]

    sections = []
    if no_assignee:
        sections.append({"emoji": "🆕", "title": "Без исполнителя", "items": no_assignee})
    if no_due:
        sections.append({"emoji": "📆", "title": "Без срока", "items": no_due, "show_who": True})
    if not sections:
        log.info("triage digest: nothing to report")
        return 0

    return _emit(engine, store, "triage", _ns(skey, "group"), {"sections": sections},
                 day=today.isoformat(), room=room)


def stale(engine, gl, group_id: str, store, days: int = STALE_DAYS, *,
          today: dt.date | None = None, weekly_day: int = WEEKLY_DAY, holidays=frozenset(),
          room=None, skey: str = "") -> int:
    """Shared room, weekly: open issues untouched for `days` days (full overview)."""
    today, skip = _weekly_skip(today, weekly_day, holidays)
    if skip:
        log.info("stale digest: not the weekly day (%s) — skip", today.isoformat())
        return 0
    cutoff = (today - dt.timedelta(days=days))
    issues = gl.group_issues(
        group_id, state="opened", scope="all",
        updated_before=cutoff.isoformat() + "T23:59:59Z",
    )
    rows = []
    for issue in sorted(issues, key=lambda i: i.get("updated_at", "")):
        row = _row(issue)
        try:
            row["idle"] = (today - _date(issue["updated_at"])).days
        except Exception:                       # noqa: BLE001 — missing/odd timestamp
            row["idle"] = None
        rows.append(row)
    if not rows:
        log.info("stale digest: nothing to report")
        return 0

    return _emit(engine, store, "stale", _ns(skey, "group"),
                 {"days": days, "sections": [{"emoji": "🕸", "title": "Без активности", "items": rows}]},
                 day=today.isoformat(), room=room)


def _percentile(values: list[int], q: float) -> int | None:
    """Nearest-rank percentile (q in 0..1). None for an empty list."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(q * len(ordered)))
    return ordered[rank - 1]


def _median(values: list[int]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    return ordered[mid] if n % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def metrics(engine, gl, group_id: str, store, window: int = METRICS_WINDOW,
            *, room=None, skey: str = "") -> int:
    """Shared room: weekly issue-flow thermometer (Free-tier, Issues API only)."""
    today = _today()
    since = today - dt.timedelta(days=window)

    opened = gl.group_issues(group_id, state="opened", scope="all")
    closed = gl.group_issues(
        group_id, state="closed", scope="all",
        updated_after=since.isoformat() + "T00:00:00Z",
    )
    closed_in = [i for i in closed if i.get("closed_at") and _date(i["closed_at"]) >= since]

    ages = [(today - _date(i["created_at"])).days for i in opened if i.get("created_at")]
    cycles = [(_date(i["closed_at"]) - _date(i["created_at"])).days
              for i in closed_in if i.get("created_at") and i.get("closed_at")]

    extra = {
        "window": window,
        "throughput": len(closed_in),
        "wip": sum(1 for i in opened if IN_PROGRESS in (i.get("labels") or [])),
        "open_total": len(opened),
        "age_med": _median(ages),
        "cycle_p85": _percentile(cycles, 0.85),
    }
    # Metrics are a daily-stable snapshot; key by ISO week so it posts once a week.
    week_key = "%s-W%02d" % today.isocalendar()[:2]
    return _emit(engine, store, "metrics", _ns(skey, week_key), extra, room=room)
