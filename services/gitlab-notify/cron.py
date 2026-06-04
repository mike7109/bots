"""Daily poll for things webhooks can't tell us about.

Webhooks only cover issue open/close/reopen. Date-driven reminders have no
event to hang off, so we poll the API once a day:

    due_soon   issue whose due_date is *tomorrow*   -> shared room
    overdue    open issue whose due_date is past     -> personal DM (deduped)

Subcommands pick which pass to run; no arg runs them all (the daily job):

    python cron.py            # due_soon + overdue
    python cron.py due        # only due_soon
    python cron.py overdue    # only overdue

overdue is deduped through botkit.store so a second run on the same day is a
no-op — see Store for the one-reminder-per-item-per-day semantics. This is
also the right place to reconcile Task open/close/reopen later, since GitLab
17.6 doesn't webhook Task work items.
"""
import datetime as dt
import logging
import sys

from botkit.config import env
from botkit.gitlab import GitLabClient
from botkit.notify.event import Event
from botkit.store import Store
from wiring import build_engine

log = logging.getLogger("gitlab-notify.cron")


def _event(issue: dict, *, kind: str, action: str) -> Event:
    """Build an Event from a GitLab REST issue object."""
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


def run_due_soon(engine, issues: list[dict]) -> int:
    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    sent = 0
    for issue in issues:
        if issue.get("due_date") != tomorrow:
            continue
        engine.handle(_event(issue, kind="due_soon", action="due"))
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
    if cmd not in ("all", "due", "overdue"):
        sys.exit(f"usage: cron.py [due|overdue]   (got {cmd!r})")

    engine = build_engine()
    gl = GitLabClient(env("GITLAB_URL", required=True), env("GITLAB_TOKEN", required=True))
    group_id = env("GITLAB_GROUP_ID", "3")
    issues = gl.group_issues(group_id, state="opened", scope="all")

    if cmd in ("all", "due"):
        run_due_soon(engine, issues)
    if cmd in ("all", "overdue"):
        store = Store()
        try:
            run_overdue(engine, issues, store)
        finally:
            store.close()


if __name__ == "__main__":
    logging.basicConfig(level=env("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    main(sys.argv[1:])
