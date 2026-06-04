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


def _event(issue: dict, *, kind: str, action: str) -> Event:
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
    )


def run_due_soon(engine, issues: list[dict], store: Store) -> int:
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    sent = 0
    for issue in issues:
        if issue.get("due_date") != tomorrow:
            continue
        key = issue.get("id")
        if store.already_sent("due_soon", key):
            continue
        result = engine.handle(_event(issue, kind="due_soon", action="due"))
        if result.get("sent"):
            store.mark_sent("due_soon", key)
            sent += 1
    log.info("due tomorrow (%s): %d reminder(s) sent", tomorrow, sent)
    return sent


def run_overdue(engine, issues: list[dict], store: Store) -> int:
    """Open issues whose due_date is already in the past -> DM, once per day."""
    today = dt.date.today().isoformat()
    sent = 0
    for issue in issues:
        due = issue.get("due_date")
        if not due or due >= today:
            continue
        key = issue.get("id")  # global issue id — stable across renames/moves
        if store.already_sent("overdue", key):
            continue
        result = engine.handle(_event(issue, kind="overdue", action="overdue"))
        if result.get("sent"):
            store.mark_sent("overdue", key)
            sent += 1
        else:
            log.warning("overdue #%s not delivered: %s", issue.get("iid"), result)
    log.info("overdue (before %s): %d reminder(s) sent", today, sent)
    return sent


def main(argv: list[str]) -> None:
    cmd = argv[0] if argv else "all"
    if cmd not in ("all", *PASSES):
        sys.exit(f"usage: cron.py [{'|'.join(PASSES)}]   (got {cmd!r})")

    engine = build_engine()
    gl = GitLabClient(env("GITLAB_URL", required=True), env("GITLAB_TOKEN", required=True))
    group_id = env("GITLAB_GROUP_ID", "3")

    wanted = DAILY if cmd == "all" else (cmd,)

    # Most passes work off the same "all open issues" snapshot — fetch it once.
    issues = gl.group_issues(group_id, state="opened", scope="all")
    store = Store()
    try:
        if "due" in wanted:
            run_due_soon(engine, issues, store)
        if "overdue" in wanted:
            run_overdue(engine, issues, store)
        if "digest" in wanted:
            digests.personal(engine, issues, store)
        if "team" in wanted:
            digests.team(engine, issues, store)
        if "triage" in wanted:
            digests.triage(engine, issues, store)
        if "stale" in wanted:
            digests.stale(engine, gl, group_id, store, days=int(env("STALE_DAYS", str(digests.STALE_DAYS))))
        if "metrics" in wanted:
            digests.metrics(engine, gl, group_id, store)
    finally:
        store.close()


if __name__ == "__main__":
    logging.basicConfig(level=env("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    main(sys.argv[1:])
