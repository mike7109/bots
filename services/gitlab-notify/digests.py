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

IN_PROGRESS = "workflow::in progress"   # the team's agreed "started" label
TEAM_SOON_DAYS = 3                      # team digest "near deadlines" window
PERSONAL_SOON_DAYS = 7                  # personal digest "this week" window
STALE_DAYS = 14                         # no activity for this long -> stale
METRICS_WINDOW = 7                      # metrics look-back, days


# --- shared helpers ------------------------------------------------------
def _today() -> dt.date:
    return dt.date.today()


def _date(s: str) -> dt.date:
    return dt.date.fromisoformat(str(s)[:10])


def _row(issue: dict) -> dict:
    """The per-issue shape templates iterate over."""
    return {
        "iid": issue.get("iid"),
        "title": issue.get("title", ""),
        "url": issue.get("web_url", ""),
        "due": issue.get("due_date"),
        "labels": issue.get("labels", []),
        "assignees": [a.get("username") for a in (issue.get("assignees") or []) if a.get("username")],
    }


def _by_due(rows: list[dict]) -> list[dict]:
    return sorted(rows, key=lambda r: r["due"] or "")


def _emit(engine, store, kind: str, dedup_key: str, extra: dict, assignees=None) -> int:
    """Build the digest Event, dispatch it, and mark it sent on success."""
    if store.already_sent(kind, dedup_key):
        log.info("%s [%s]: already sent today", kind, dedup_key)
        return 0
    event = Event(kind=kind, action="digest", assignees=assignees or [], extra=extra)
    result = engine.handle(event)
    if result.get("sent"):
        store.mark_sent(kind, dedup_key)
        return 1
    log.warning("%s [%s] not delivered: %s", kind, dedup_key, result)
    return 0


# --- passes --------------------------------------------------------------
def personal(engine, issues: list[dict], store) -> int:
    """Per-assignee DM: their open issues grouped by how soon they're due."""
    today = _today().isoformat()
    horizon = (_today() + dt.timedelta(days=PERSONAL_SOON_DAYS)).isoformat()

    by_user: dict[str, list[dict]] = {}
    for issue in issues:
        for a in (issue.get("assignees") or []):
            login = a.get("username")
            if login:
                by_user.setdefault(login, []).append(issue)

    sent = 0
    for login, items in sorted(by_user.items()):
        rows = [_row(i) for i in items]
        overdue = [r for r in rows if r["due"] and r["due"] < today]
        due_today = [r for r in rows if r["due"] == today]
        soon = [r for r in rows if r["due"] and today < r["due"] <= horizon]
        no_date = [r for r in rows if not r["due"]]

        sections = []
        if overdue:
            sections.append({"emoji": "⏰", "title": "Просрочено", "items": _by_due(overdue)})
        if due_today:
            sections.append({"emoji": "📅", "title": "Сегодня", "items": due_today})
        if soon:
            sections.append({"emoji": "🔜", "title": "На этой неделе", "items": _by_due(soon)})
        if no_date:
            sections.append({"emoji": "📋", "title": "Без срока", "items": no_date})
        if not sections:
            continue

        sent += _emit(
            engine, store, "digest_personal", login,
            {"date": today, "sections": sections, "total": len(rows)},
            assignees=[login],
        )
    log.info("personal digest: %d sent", sent)
    return sent


def team(engine, issues: list[dict], store) -> int:
    """Shared room standup overview across the whole group."""
    today = _today().isoformat()
    horizon = (_today() + dt.timedelta(days=TEAM_SOON_DAYS)).isoformat()
    rows = [_row(i) for i in issues]

    # Each issue lands in exactly one section, most-urgent-first, so a single
    # issue can't show up under both "near deadline" and "in progress".
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
    if not sections:
        log.info("team digest: nothing to report")
        return 0

    return _emit(engine, store, "digest_team", "group",
                 {"date": today, "sections": sections, "open_total": len(rows)})


def triage(engine, issues: list[dict], store) -> int:
    """Shared room: issues that need a human decision (unassigned / no deadline)."""
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

    return _emit(engine, store, "triage", "group", {"sections": sections})


def stale(engine, gl, group_id: str, store, days: int = STALE_DAYS) -> int:
    """Shared room: open issues untouched for `days` days."""
    cutoff = (_today() - dt.timedelta(days=days))
    issues = gl.group_issues(
        group_id, state="opened", scope="all",
        updated_before=cutoff.isoformat() + "T23:59:59Z",
    )
    rows = []
    for issue in sorted(issues, key=lambda i: i.get("updated_at", "")):
        row = _row(issue)
        try:
            row["idle"] = (_today() - _date(issue["updated_at"])).days
        except Exception:                       # noqa: BLE001 — missing/odd timestamp
            row["idle"] = None
        rows.append(row)
    if not rows:
        log.info("stale digest: nothing to report")
        return 0

    return _emit(engine, store, "stale", "group",
                 {"days": days, "sections": [{"emoji": "🕸", "title": "Без активности", "items": rows}]})


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


def metrics(engine, gl, group_id: str, store, window: int = METRICS_WINDOW) -> int:
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
    return _emit(engine, store, "metrics", week_key, extra)
