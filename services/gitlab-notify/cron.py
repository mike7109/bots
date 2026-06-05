"""Daily poll for things webhooks can't tell us about.

Webhooks only cover issue open/close/reopen. Everything date-driven or
aggregated has no event to hang off, so we poll the API. Each subcommand is one
pass; `all` runs the daily set (metrics is weekly, so it's opt-in):

    python cron.py            # all daily passes
    python cron.py due        # due tomorrow -> room
    python cron.py overdue    # past due -> personal DM (deduped)
    python cron.py digest     # personal "what's on me" DM per assignee
    python cron.py team       # standup overview -> room
    python cron.py triage     # untriaged issues -> room
    python cron.py stale      # issues with no activity for N days -> room
    python cron.py metrics    # weekly issue-flow snapshot -> room

Everything dedups through botkit.store so a second run the same day is a no-op
(see Store and digests.py). This is also the right place to reconcile Task
open/close/reopen later, since GitLab 17.6 doesn't webhook Task work items.
"""
import datetime as dt
import logging
import sys

import digests
import keys
from botkit.config import env
from botkit.gitlab import GitLabClient
from botkit.notify.event import Event
from botkit.store import Store
from wiring import build_context

log = logging.getLogger("gitlab-notify.cron")

# Daily passes run by a bare `python cron.py`. `metrics` is weekly -> not here.
DAILY = ("due", "overdue", "digest", "team", "triage", "stale")
PASSES = DAILY + ("metrics",)


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


def _remind(engine, issues: list[dict], store: Store, *, kind: str, action: str,
            due_predicate, room=None, skey: str = "", warn_undelivered: bool = False) -> int:
    """Filter -> per-day dedup -> handle -> mark loop, shared by the reminders.

    `due_predicate(due)` selects which issues to remind on (due-tomorrow for the
    soon pass, past-due for overdue). The dedup key is the global issue id
    namespaced by source (`keys.ns`), so a second run the same day is a no-op and
    two groups sharing an id don't collide. `warn_undelivered` logs each issue
    the engine failed to deliver (overdue only, matching the original).
    """
    sent = 0
    for issue in issues:
        if not due_predicate(issue.get("due_date")):
            continue
        key = keys.ns(skey, issue.get("id"))   # global issue id — stable across renames
        if store.already_sent(kind, key):
            continue
        result = engine.handle(_event(issue, kind=kind, action=action, room=room))
        if result.get("sent"):
            store.mark_sent(kind, key)
            sent += 1
        elif warn_undelivered:
            log.warning("overdue #%s not delivered: %s", issue.get("iid"), result)
    return sent


def run_due_soon(engine, issues: list[dict], store: Store, *, room=None, skey: str = "") -> int:
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    sent = _remind(engine, issues, store, kind=keys.DUE_SOON, action="due",
                   due_predicate=lambda due: due == tomorrow, room=room, skey=skey)
    log.info("due tomorrow (%s): %d reminder(s) sent", tomorrow, sent)
    return sent


def run_overdue(engine, issues: list[dict], store: Store, *, room=None, skey: str = "") -> int:
    """Open issues whose due_date is already in the past -> DM, once per day."""
    today = dt.date.today().isoformat()
    sent = _remind(engine, issues, store, kind=keys.OVERDUE, action="overdue",
                   due_predicate=lambda due: bool(due) and due < today,
                   room=room, skey=skey, warn_undelivered=True)
    log.info("overdue (before %s): %d reminder(s) sent", today, sent)
    return sent


def run_one(engine, gl, group_id: str, store, name: str, *,
            force: bool = False, anchor: bool = False, room=None, skey: str = "") -> int:
    """Run a single pass by name. Shared by the scheduler and the admin "send now".

    `room`/`skey` route this source's events to its room and namespace dedup so
    groups don't collide. The caller decides timing: `force` (manual trigger) or
    `anchor` (scheduled anchor day) -> digests send a full overview; else a delta.
    """
    issues = gl.group_issues(group_id, state="opened", scope="all")
    wd = dt.date.today().weekday()
    full = force or anchor
    ds = dict(anchor_days={wd} if full else set(), holidays=frozenset(), skip_weekends=False,
              room=room, skey=skey)
    wk = dict(weekly_day=wd, holidays=frozenset(), room=room, skey=skey)
    if name == "due":
        return run_due_soon(engine, issues, store, room=room, skey=skey)
    if name == "overdue":
        return run_overdue(engine, issues, store, room=room, skey=skey)
    if name == "digest":
        return digests.personal(engine, issues, store, **ds)
    if name == "team":
        return digests.team(engine, issues, store, **ds)
    if name == "triage":
        return digests.triage(engine, issues, store, **wk)
    if name == "stale":
        days = int(engine.settings.pass_schedule("stale").get("days_idle", digests.STALE_DAYS))
        return digests.stale(engine, gl, group_id, store, days=days, **wk)
    if name == "metrics":
        return digests.metrics(engine, gl, group_id, store, room=room, skey=skey)
    raise ValueError(f"unknown pass: {name}")


def main(argv: list[str]) -> None:
    args = list(argv)
    force = "--force" in args                  # let host-cron override the owner check
    args = [a for a in args if a != "--force"]
    cmd = args[0] if args else "all"
    if cmd not in ("all", *PASSES):
        sys.exit(f"usage: cron.py [--force] [{'|'.join(PASSES)}]   (got {cmd!r})")

    ctx = build_context()
    store = ctx.store                         # shared DB (dedup + settings live together)
    settings = ctx.settings
    today = dt.date.today()
    today_iso = today.isoformat()
    wanted = DAILY if cmd == "all" else (cmd,)
    url = settings.conn_value("gitlab_url")

    # Single schedule owner: if «Авторассылки» (the in-process scheduler) is on,
    # IT owns the schedule. Host-cron is only meant for when that's off — running
    # both would double-send in the network window. So bail out (exit 0) unless
    # --force is passed.
    if settings.get_global().get("scheduler_on", True) and not force:
        log.warning("scheduler_on=True: in-process scheduler owns the schedule; "
                    "host-cron exiting without sending (use --force to override)")
        store.close()
        return

    # Host-cron fallback (when «Авторассылки» is off, or forced): run the
    # requested passes for every configured source. Even when forced we honour +
    # write the SAME `sched` ledger the scheduler uses, so the two coordinate and
    # never double-send the same pass on the same day. Digests send full on a
    # pass's anchor day, a delta otherwise.
    try:
        for src in ctx.sources.enabled():
            gid = src.get("group_id")
            if not gid:
                continue
            gl = GitLabClient(url, src.get("token", ""))
            for name in wanted:
                schedkey = keys.ns(src["id"], name)
                if store.already_sent(keys.SCHED, schedkey, day=today_iso):
                    log.info("cron %s/%s: already fired today — skip", src["id"], name)
                    continue
                anchor = today.weekday() in settings.pass_schedule(name).get("anchor_days", [])
                run_one(ctx.engine, gl, gid, store, name,
                        anchor=anchor, room=src.get("room"), skey=src["id"])
                store.mark_sent(keys.SCHED, schedkey, day=today_iso)
                store.record_run(schedkey, today_iso, ok=True)
    finally:
        store.close()


if __name__ == "__main__":
    logging.basicConfig(level=env("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    main(sys.argv[1:])
