"""Daily poll for things webhooks can't tell us about.

Currently: issues/tasks whose due date is *tomorrow* -> a Matrix reminder
(`due_soon` event). This is also the right place to reconcile Task
open/close/reopen, since GitLab 17.6 doesn't webhook Task work items.

Run once a day, e.g. from host cron / a systemd timer:
    docker compose -f deploy/docker-compose.yml run --rm gitlab-notify python cron.py
"""
import datetime as dt
import logging

from botkit.config import env
from botkit.gitlab import GitLabClient
from botkit.notify.event import Event
from wiring import build_engine


def main() -> None:
    engine = build_engine()
    gl = GitLabClient(env("GITLAB_URL", required=True), env("GITLAB_TOKEN", required=True))
    group_id = env("GITLAB_GROUP_ID", "3")

    tomorrow = (dt.date.today() + dt.timedelta(days=1)).isoformat()
    issues = gl.group_issues(group_id, state="opened", scope="all")

    reminded = 0
    for issue in issues:
        if issue.get("due_date") != tomorrow:
            continue
        assignees = [a.get("username") for a in (issue.get("assignees") or []) if a.get("username")]
        event = Event(
            kind="due_soon",
            action="due",
            project=(issue.get("references") or {}).get("full", "").rsplit("#", 1)[0],
            iid=str(issue.get("iid", "")),
            title=issue.get("title", ""),
            url=issue.get("web_url", ""),
            state=issue.get("state", ""),
            labels=issue.get("labels", []),
            assignees=assignees,
            due=issue.get("due_date"),
        )
        engine.handle(event)
        reminded += 1

    logging.getLogger("gitlab-notify.cron").info(
        "due tomorrow (%s): %d reminder(s) sent", tomorrow, reminded
    )


if __name__ == "__main__":
    logging.basicConfig(level=env("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
    main()
