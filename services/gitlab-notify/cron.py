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
from botkit.config import env
from botkit.gitlab import GitLabClient
from botkit.notify.event import Event
from botkit.store import Store
from wiring import build_engine

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


def run_due_soon(engine, issues: list[dict], store: Store, *, room=None, skey: str = "") -> int:
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    sent = 0
    for issue in issues:
        if issue.get("due_date") != tomorrow:
            continue
        key = digests._ns(skey, issue.get("id"))
        if store.already_sent("due_soon", key):
            continue
        result = engine.handle(_event(issue, kind="due_soon", action="due", room=room))
        if result.get("sent"):
            store.mark_sent("due_soon", key)
            sent += 1
    log.info("due tomorrow (%s): %d reminder(s) sent", tomorrow, sent)
    return sent


def run_overdue(engine, issues: list[dict], store: Store, *, room=None, skey: str = "") -> int:
    """Open issues whose due_date is already in the past -> DM, once per day."""
    today = dt.date.today().isoformat()
    sent = 0
    for issue in issues:
        due = issue.get("due_date")
        if not due or due >= today:
            continue
        key = digests._ns(skey, issue.get("id"))  # global issue id — stable across renames
        if store.already_sent("overdue", key):
            continue
        result = engine.handle(_event(issue, kind="overdue", action="overdue", room=room))
        if result.get("sent"):
            store.mark_sent("overdue", key)
            sent += 1
        else:
            log.warning("overdue #%s not delivered: %s", issue.get("iid"), result)
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
    cmd = argv[0] if argv else "all"
    if cmd not in ("all", *PASSES):
        sys.exit(f"usage: cron.py [{'|'.join(PASSES)}]   (got {cmd!r})")

    engine = build_engine()
    store = engine.store                      # shared DB (dedup + settings live together)
    settings = engine.settings
    today = dt.date.today()
    wanted = DAILY if cmd == "all" else (cmd,)
    url = settings.conn_value("gitlab_url")

    # Host-cron fallback (if you drive cron.py from host cron instead of the
    # built-in scheduler): run the requested passes
    # for every configured source. The host cron schedule decides *when*; digests
    # send full on a pass's anchor day, a delta otherwise.
    try:
        for src in engine.sources.enabled():
            gid = src.get("group_id")
            if not gid:
                continue
            gl = GitLabClient(url, src.get("token", ""))
            for name in wanted:
                anchor = today.weekday() in settings.pass_schedule(name).get("anchor_days", [])
                run_one(engine, gl, gid, store, name,
                        anchor=anchor, room=src.get("room"), skey=src["id"])
    finally:
        store.close()


if __name__ == "__main__":
    logging.basicConfig(level=env("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    main(sys.argv[1:])
