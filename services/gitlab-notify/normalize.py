"""Translate a GitLab webhook payload into a botkit Event.

Only issue events arrive by webhook. NOTE: GitLab 17.6 does NOT emit webhooks
for Task work items (see services/gitlab-expand-tasks/expand.py — Tasks are
only reachable via GraphQL). Task open/close/reopen and due-date reminders are
therefore produced by cron.py polling the API, not here.
"""
from __future__ import annotations

import re

from botkit.notify.event import Event

# GitLab issue `action` -> our action vocabulary. `update` (every label/assignee
# change fires one) is mapped too, but routed ONLY to watched-issue rooms by the
# webhook handler (app.py) — it never reaches the normal source room, so the
# regular channel stays quiet. open/close/reopen go to the source room as before.
_ISSUE_ACTION = {"open": "open", "reopen": "reopen", "close": "close", "update": "update"}

# @login parser for comment bodies. Allow dots/underscores/hyphens (e.g.
# @m.bahmutskij); the lookbehind blocks matches inside emails/paths/@@
# (user@host, a/@b, a@@b). Group mentions (@grp/sub) capture only "grp" and are
# harmlessly dropped at resolution time (Identity.known_matrix_id -> None).
_MENTION_RE = re.compile(r"(?<![\w@./-])@([A-Za-z0-9][A-Za-z0-9._-]*)")
_MENTION_SKIP = {"all", "here", "everyone", "channel", "room"}


def _mentions(text: str, limit: int = 20) -> list[str]:
    """Distinct @-logins in a comment body, order-preserving, fan-out bounded."""
    out, seen = [], set()
    for raw in _MENTION_RE.findall(text or ""):
        login = raw.rstrip("._-")                       # trailing '@user.' / '@user-' punctuation
        if not login or login.lower() in _MENTION_SKIP or login in seen:
            continue
        seen.add(login)
        out.append(login)
        if len(out) >= limit:                           # bound ping fan-out
            break
    return out


def normalize(payload: dict) -> Event | None:
    kind = payload.get("object_kind")
    if kind == "issue":
        return _issue(payload)
    if kind == "note":
        return _note(payload)
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


def _note(payload: dict) -> Event | None:
    """A NEW comment on an issue. Only new human comments qualify: skip notes on
    MRs/commits/snippets, machine-generated system notes (label/assignee changes —
    already covered by issue `update`), comment EDITS/re-fires, and empty bodies.
    Labels live UNDER the `issue` object here (NOT top-level as for issue events),
    so watched-detection reads them from there. Routed watch-rooms-only by app.py."""
    attr = payload.get("object_attributes", {})
    if attr.get("noteable_type") != "Issue":
        return None
    if attr.get("system"):
        return None
    if attr.get("action") not in (None, "create"):      # new comments only
        return None
    body = (attr.get("note") or "").strip()
    if not body:
        return None

    issue = payload.get("issue") or {}
    labels = [lbl.get("title", "") for lbl in (issue.get("labels") or [])]

    return Event(
        kind="note",
        action="create",
        project=payload.get("project", {}).get("path_with_namespace", ""),
        iid=str(issue.get("iid", "")),
        title=issue.get("title", ""),
        url=attr.get("url", ""),                         # deep-link to the comment (…/issues/17#note_1241)
        labels=labels,
        author=(payload.get("user") or {}).get("username"),   # the commenter
        comment=body,
        mention_logins=_mentions(body),
    )
