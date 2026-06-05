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
from dataclasses import asdict

from botkit.notify.event import Event

import keys

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
    carry several. REST label order isn't guaranteed, so picking "the first"
    would make the chosen column — and thus delta "moved" detection —
    non-deterministic run-to-run. We pick the alphabetically-smallest matching
    column instead, so the same set of labels always maps to the same column.
    None means it's in the default Open list (no workflow label yet).
    """
    cols = [lbl[len(WORKFLOW_PREFIX):] for lbl in (labels or [])
            if lbl.startswith(WORKFLOW_PREFIX)]
    return min(cols) if cols else None


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


def _emit(engine, store, kind: str, dedup_key: str, extra: dict, assignees=None,
          *, day=None, room=None, commit: bool = True, dry: bool = False,
          recipients=None) -> dict:
    """Build the digest Event and dispatch it (mark sent on success).

    Returns a uniform result dict (see `_result`). `commit=False` bypasses the
    per-pass dedup gate and skips `mark_sent` (stateless manual run). `dry=True`
    renders a real-data sample without dispatching.
    """
    event = Event(kind=kind, action="digest", assignees=assignees or [], extra=extra, room=room)
    if dry:
        return _result(0, "ok", recipients or _room_recipients(room),
                       html=_render(engine, event))
    if commit and store.already_sent(kind, dedup_key, day=day):
        log.info("%s [%s]: already sent today", kind, dedup_key)
        return _result(0, "ok", recipients or _room_recipients(room))
    result = engine.handle(event)
    sent = 1 if result.get("sent") else 0
    if sent and commit:
        store.mark_sent(kind, dedup_key, day=day)
    if not sent:
        log.warning("%s [%s] not delivered: %s", kind, dedup_key, result)
    return _result(sent, "ok", recipients or _room_recipients(room),
                   html=_render(engine, event))


# --- manual "Run now" plumbing: uniform result + real-data preview -------
def _result(sent: int, reason: str, recipients=None, *, html=None) -> dict:
    """The uniform shape every pass returns: {sent, reason, recipients, html}.

    `reason` ∈ {ok, no_changes, no_baseline, empty, error}. `recipients` is
    ["room:<id>"] for room passes or the list of assignee logins for dm passes.
    `html` is a rendered real-data sample (set for dry AND real sends).
    """
    return {"sent": sent, "reason": reason, "recipients": list(recipients or []), "html": html}


def _room_recipients(room) -> list[str]:
    """Recipient label for a room pass: ["room:<id>"] (or [] if no room)."""
    return [f"room:{room}"] if room else ["room:default"]


def _render(engine, event: Event) -> str | None:
    """Render the real-data Matrix sample for an event via the matched rule's
    template (the same template a real send would use). Best-effort: returns
    None if no renderer / no matching rule / a render error."""
    renderer = getattr(engine, "renderer", None)
    match = getattr(engine, "match", None)
    if renderer is None or match is None:
        return None
    rule = match(event)
    template = (rule or {}).get("template") if rule else None
    if not template:
        return None
    try:
        return renderer.render(template, "matrix", asdict(event))
    except Exception as e:                              # noqa: BLE001 — preview must never raise
        log.warning("dry render of %s failed: %s", template, e)
        return None


# --- per-issue reminders (due-soon / overdue) ----------------------------
# These two passes share `_remind` (filter -> per-day dedup -> handle -> mark).
# They live here (not cron.py) so the pass registry can import a single module.
def _event(issue: dict, *, kind: str, action: str, room=None) -> Event:
    """Build a single-issue Event from a GitLab REST issue object."""
    assignees = [a.get("username") for a in (issue.get("assignees") or []) if a.get("username")]
    return Event(
        kind=kind,
        action=action,
        project=(issue.get("references") or {}).get("full", "").rsplit("#", 1)[0],
        iid=str(issue.get("iid", "")),
        title=issue.get("title", ""),
        url=issue.get("web_url", ""),
        state=issue.get("state", ""),
        labels=issue.get("labels", []),
        assignees=assignees,
        due=issue.get("due_date"),
        room=room,
    )


def _remind(engine, issues: list[dict], store, *, kind: str, action: str,
            due_predicate, room=None, skey: str = "", warn_undelivered: bool = False,
            commit: bool = True, dry: bool = False):
    """Filter -> per-day dedup -> handle -> mark loop, shared by the reminders.

    `due_predicate(due)` selects which issues to remind on (due-tomorrow for the
    soon pass, past-due for overdue). The dedup key is the global issue id
    namespaced by source (`keys.ns`), so a second run the same day is a no-op and
    two groups sharing an id don't collide. `warn_undelivered` logs each issue
    the engine failed to deliver (overdue only, matching the original).

    `commit=False` (manual) ignores the per-issue dedup and never marks sent, so
    a manual run re-sends every matching item statelessly. `dry=True` renders a
    sample for the first matching issue without dispatching. Returns
    `(sent, recipients, sample_html)`: `recipients` is the per-issue assignee
    logins (dm passes) or the room (room passes).
    """
    sent = 0
    recipients: list[str] = []
    sample_html = None
    room_pass = action == "due"                 # due_soon -> room; overdue -> dm
    for issue in issues:
        if not due_predicate(issue.get("due_date")):
            continue
        key = keys.ns(skey, issue.get("id"))   # global issue id — stable across renames
        if commit and store.already_sent(kind, key):
            continue
        event = _event(issue, kind=kind, action=action, room=room)
        if sample_html is None:
            sample_html = _render(engine, event)
        if room_pass:
            for r in _room_recipients(room):
                if r not in recipients:
                    recipients.append(r)
        else:
            for login in event.assignees:
                if login not in recipients:
                    recipients.append(login)
        if dry:
            continue
        result = engine.handle(event)
        if result.get("sent"):
            if commit:
                store.mark_sent(kind, key)
            sent += 1
        elif warn_undelivered:
            log.warning("overdue #%s not delivered: %s", issue.get("iid"), result)
    return sent, recipients, sample_html


def run_due_soon(engine, issues: list[dict], store, *, room=None, skey: str = "",
                 commit: bool = True, dry: bool = False) -> dict:
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    sent, recipients, html = _remind(
        engine, issues, store, kind=keys.DUE_SOON, action="due",
        due_predicate=lambda due: due == tomorrow, room=room, skey=skey,
        commit=commit, dry=dry)
    log.info("due tomorrow (%s): %d reminder(s) sent", tomorrow, sent)
    reason = "ok" if (sent or (dry and recipients)) else "empty"
    return _result(sent, reason, recipients, html=html)


def run_overdue(engine, issues: list[dict], store, *, room=None, skey: str = "",
                commit: bool = True, dry: bool = False) -> dict:
    """Open issues whose due_date is already in the past -> DM, once per day."""
    today = dt.date.today().isoformat()
    sent, recipients, html = _remind(
        engine, issues, store, kind=keys.OVERDUE, action="overdue",
        due_predicate=lambda due: bool(due) and due < today,
        room=room, skey=skey, warn_undelivered=True, commit=commit, dry=dry)
    log.info("overdue (before %s): %d reminder(s) sent", today, sent)
    reason = "ok" if (sent or (dry and recipients)) else "empty"
    return _result(sent, reason, recipients, html=html)


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
             room=None, skey: str = "", full: bool | None = None,
             dry: bool = False, commit: bool = True) -> dict:
    """Per-assignee DM, delta-driven.

    Only writes when a person's issues actually changed (new / moved column /
    due changed / crossed into overdue-or-today / removed) — no daily repeat of
    an unchanged list. On `anchor_days` (or `full=True`) it sends a full
    overview regardless. Weekends (unless skip_weekends=False) and `holidays`
    (ISO dates) are silent.

    `full`: None -> derive from the anchor day (scheduler behaviour). True/False
    -> explicit operator choice (full overview vs delta). `commit=False`
    (manual) bypasses the per-person dedup gate and writes no `sent`/`state`
    rows — repeatable + stateless. `dry=True` renders a per-recipient sample
    without dispatching. Returns the uniform `_result` dict, aggregated across
    recipients ("ok" if any would send, else the dominant reason).
    """
    today = today or _today()
    iso = today.isoformat()
    if commit and ((skip_weekends and today.weekday() >= 5) or iso in holidays):
        log.info("personal digest: quiet day (%s) — silent", iso)
        return _result(0, "empty", [])
    is_anchor = today.weekday() in anchor_days
    is_full = full if full is not None else is_anchor
    horizon = (today + dt.timedelta(days=PERSONAL_SOON_DAYS)).isoformat()

    by_user: dict[str, list[dict]] = {}
    for issue in issues:
        for a in (issue.get("assignees") or []):
            login = a.get("username")
            if login:
                by_user.setdefault(login, []).append(issue)

    sent = 0
    recipients: list[str] = []          # logins this run sent to (or would send to)
    reasons: list[str] = []             # per-person would-be reason (for aggregation)
    sample_html = None                  # first recipient's rendered message
    for login, items in sorted(by_user.items()):
        pkey = keys.ns(skey, login)
        if commit and store.already_sent(keys.DIGEST_PERSONAL, pkey, day=iso):  # one DM per person per day
            continue
        rows = [_row(i) for i in items]
        current = _snapshot(rows, iso, horizon)
        prev = store.get_state(keys.DIGEST_PERSONAL, pkey)

        if is_full:
            # Full overview. The FULL pass is a pure reader: it sends the whole
            # picture but NEVER touches the delta baseline — the delta pass is the
            # sole owner/writer of that snapshot (see the delta path below).
            extra = {"mode": "full", "date": iso, "total": len(rows),
                     "sections": _full_sections(rows, iso, horizon)}
        elif prev is None:
            # Delta with no baseline. Scheduler (commit): learn it silently so
            # the next change is a real delta, not "all new". Manual (no commit):
            # there's nothing to diff against — report it, don't seed.
            if commit:
                store.set_state(keys.DIGEST_PERSONAL, pkey, current)
            reasons.append("no_baseline")
            continue
        else:
            changes = _diff(prev, current)
            if not any(changes.values()):
                reasons.append("no_changes")
                continue                                    # nothing changed -> silent
            extra = {"mode": "delta", "date": iso, "changes": changes}

        event = Event(kind=keys.DIGEST_PERSONAL, action="digest", assignees=[login], extra=extra, room=room)
        if sample_html is None:
            sample_html = _render(engine, event)
        reasons.append("ok")
        recipients.append(login)
        if dry:
            continue
        result = engine.handle(event)
        if result.get("sent"):
            sent += 1
            if commit:
                store.mark_sent(keys.DIGEST_PERSONAL, pkey, day=iso)
                if not is_full:
                    # BASELINE OWNERSHIP: only the delta pass advances the baseline.
                    # A full send is a read-only overview and must leave it intact.
                    store.set_state(keys.DIGEST_PERSONAL, pkey, current)
        else:
            log.warning("%s [%s] not delivered: %s", keys.DIGEST_PERSONAL, pkey, result)
    log.info("personal digest: %d sent (%s)", sent, "full" if is_full else "delta")
    reason = _aggregate_reason(reasons)
    # dry/real: recipients = logins it would DM (only the "ok" ones).
    return _result(sent, reason, recipients, html=sample_html)


def _aggregate_reason(reasons: list[str]) -> str:
    """Fold per-recipient reasons into one: "ok" if any would send, else the
    most common of the rest (empty list -> "empty")."""
    if "ok" in reasons:
        return "ok"
    if not reasons:
        return "empty"
    return max(set(reasons), key=reasons.count)


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
         room=None, skey: str = "", full: bool | None = None,
         dry: bool = False, commit: bool = True) -> dict:
    """Shared room standup digest, delta-driven (same rules as `personal`).

    Writes only when the group's issues changed, except on `anchor_days` (or
    `full=True`) where it posts a full standup overview. Weekends/holidays are
    silent. `commit=False` (manual) bypasses the per-day dedup and writes no
    ledger/baseline; `dry=True` renders a sample without dispatching. Returns
    the uniform `_result` dict.
    """
    today = today or _today()
    iso = today.isoformat()
    rcpt = _room_recipients(room)
    if commit and ((skip_weekends and today.weekday() >= 5) or iso in holidays):
        log.info("team digest: quiet day (%s) — silent", iso)
        return _result(0, "empty", [])
    is_anchor = today.weekday() in anchor_days
    is_full = full if full is not None else is_anchor
    sec_horizon = (today + dt.timedelta(days=TEAM_SOON_DAYS)).isoformat()
    snap_horizon = (today + dt.timedelta(days=PERSONAL_SOON_DAYS)).isoformat()

    gkey = keys.ns(skey, "group")
    rows = [_row(i) for i in issues]
    current = _snapshot(rows, iso, snap_horizon)
    prev = store.get_state(keys.DIGEST_TEAM, gkey)

    if commit and store.already_sent(keys.DIGEST_TEAM, gkey, day=iso):
        return _result(0, "ok", rcpt)
    if is_full:
        # Full overview — read-only: the full pass never writes the baseline (the
        # delta pass is the sole owner; see the gated set_state at the tail).
        sections = _team_sections(rows, iso, sec_horizon)
        if not sections:
            return _result(0, "empty", [])
        extra = {"mode": "full", "date": iso, "open_total": len(rows), "sections": sections}
    elif prev is None:
        # Delta with no baseline. Scheduler learns it silently; manual reports it.
        if commit:
            store.set_state(keys.DIGEST_TEAM, gkey, current)
        return _result(0, "no_baseline", [])
    else:
        changes = _diff(prev, current)
        if not any(changes.values()):
            return _result(0, "no_changes", [])
        extra = {"mode": "delta", "date": iso, "changes": changes}

    event = Event(kind=keys.DIGEST_TEAM, action="digest", extra=extra, room=room)
    if dry:
        return _result(0, "ok", rcpt, html=_render(engine, event))
    result = engine.handle(event)
    sent = 1 if result.get("sent") else 0
    if sent and commit:
        store.mark_sent(keys.DIGEST_TEAM, gkey, day=iso)
        if not is_full:
            # BASELINE OWNERSHIP: only the delta pass advances the baseline; a
            # full send is read-only and leaves the delta's snapshot untouched.
            store.set_state(keys.DIGEST_TEAM, gkey, current)
    if not sent:
        log.warning("%s [%s] not delivered: %s", keys.DIGEST_TEAM, gkey, result)
    return _result(sent, "ok", rcpt, html=_render(engine, event))


def _weekly_skip(today: dt.date | None, weekly_day: int, holidays) -> tuple[dt.date, bool]:
    """Hygiene digests run once a week. Return (today, should_skip)."""
    today = today or _today()
    skip = today.weekday() != weekly_day or today.isoformat() in holidays
    return today, skip


def triage(engine, issues: list[dict], store, *, today: dt.date | None = None,
           weekly_day: int = WEEKLY_DAY, holidays=frozenset(), room=None, skey: str = "",
           full: bool | None = None, dry: bool = False, commit: bool = True) -> dict:
    """Shared room, weekly: issues that need a decision (unassigned / no deadline).

    A full overview (not a delta) — once a week you want the whole hygiene
    picture, not just what changed. Runs only on `weekly_day` (default Monday),
    but a manual run (`commit=False`) ignores the weekly-day gate and the per-day
    dedup so it always re-sends. `full` is accepted for a uniform call site but
    has no effect (single-mode pass). `dry=True` renders without dispatching.
    """
    today, skip = _weekly_skip(today, weekly_day, holidays)
    if commit and skip:
        log.info("triage digest: not the weekly day (%s) — skip", today.isoformat())
        return _result(0, "empty", [])

    no_assignee = [_row(i) for i in issues if not (i.get("assignees"))]
    no_due = [_row(i) for i in issues if (i.get("assignees")) and not i.get("due_date")]

    sections = []
    if no_assignee:
        sections.append({"emoji": "🆕", "title": "Без исполнителя", "items": no_assignee})
    if no_due:
        sections.append({"emoji": "📆", "title": "Без срока", "items": no_due, "show_who": True})
    if not sections:
        log.info("triage digest: nothing to report")
        return _result(0, "empty", [])

    return _emit(engine, store, keys.TRIAGE, keys.ns(skey, "group"), {"sections": sections},
                 day=today.isoformat(), room=room, commit=commit, dry=dry)


def stale(engine, gl, group_id: str, store, days: int = STALE_DAYS, *,
          today: dt.date | None = None, weekly_day: int = WEEKLY_DAY, holidays=frozenset(),
          room=None, skey: str = "", full: bool | None = None,
          dry: bool = False, commit: bool = True) -> dict:
    """Shared room, weekly: open issues untouched for `days` days (full overview).

    A manual run (`commit=False`) ignores the weekly-day gate and per-day dedup.
    `full` is accepted but unused (single-mode); `dry=True` renders without send.
    """
    today, skip = _weekly_skip(today, weekly_day, holidays)
    if commit and skip:
        log.info("stale digest: not the weekly day (%s) — skip", today.isoformat())
        return _result(0, "empty", [])
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
        return _result(0, "empty", [])

    return _emit(engine, store, keys.STALE, keys.ns(skey, "group"),
                 {"days": days, "sections": [{"emoji": "🕸", "title": "Без активности", "items": rows}]},
                 day=today.isoformat(), room=room, commit=commit, dry=dry)


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
            *, room=None, skey: str = "", full: bool | None = None,
            dry: bool = False, commit: bool = True) -> dict:
    """Shared room: weekly issue-flow thermometer (Free-tier, Issues API only).

    Posts once per ISO week (deduped by week_key). A manual run (`commit=False`)
    bypasses that dedup and re-sends. `full` is accepted but unused (single-mode);
    `dry=True` renders without dispatching.
    """
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
    # Metrics are a daily-stable snapshot; post once per ISO week. The `sent` PK
    # is (kind, key, day), so to dedup across the WHOLE week (not per weekday) we
    # pin `day` to the week_key itself — otherwise a manual `cron.py metrics` (or
    # repeated admin "Запустить") on a different weekday of the same week would
    # re-send the weekly report.
    week_key = "%s-W%02d" % today.isocalendar()[:2]
    return _emit(engine, store, keys.METRICS, keys.ns(skey, week_key), extra,
                 day=week_key, room=room, commit=commit, dry=dry)
