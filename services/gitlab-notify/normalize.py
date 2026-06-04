"""Translate a GitLab webhook payload into a botkit Event.

Only issue events arrive by webhook. NOTE: GitLab 17.6 does NOT emit webhooks
for Task work items (see services/gitlab-expand-tasks/expand.py — Tasks are
only reachable via GraphQL). Task open/close/reopen and due-date reminders are
therefore produced by cron.py polling the API, not here.
"""
from __future__ import annotations

from botkit.notify.event import Event

# GitLab issue `action` -> our action vocabulary. `update` is intentionally
# dropped (too noisy: every label/assignee change fires one).
_ISSUE_ACTION = {"open": "open", "reopen": "reopen", "close": "close"}


def normalize(payload: dict) -> Event | None:
    if payload.get("object_kind") == "issue":
        return _issue(payload)
    return None


def _issue(payload: dict) -> Event | None:
    attr = payload.get("object_attributes", {})
    action = _ISSUE_ACTION.get(attr.get("action"))
    if action is None:
        return None

    assignees = [a.get("username") for a in (payload.get("assignees") or []) if a.get("username")]
    labels = [lbl.get("title", "") for lbl in payload.get("labels", [])]

    return Event(
        kind="issue",
        action=action,
        project=payload.get("project", {}).get("path_with_namespace", ""),
        iid=str(attr.get("iid", "")),
        title=attr.get("title", ""),
        url=attr.get("url", ""),
        state=attr.get("state", ""),
        labels=labels,
        assignees=assignees,
        author=(payload.get("user") or {}).get("username"),
        due=attr.get("due_date"),
    )
