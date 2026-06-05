"""normalize: GitLab webhook payload -> botkit Event. Only issue events;
maps open/reopen/close; drops 'update' and non-issue kinds."""
from __future__ import annotations

from normalize import normalize


def _payload(action="open", **attr_extra):
    attr = {"iid": 7, "title": "Fix it", "url": "https://gl/x/-/issues/7",
            "state": "opened", "action": action, "due_date": "2026-06-10"}
    attr.update(attr_extra)
    return {
        "object_kind": "issue",
        "object_attributes": attr,
        "project": {"path_with_namespace": "fakspro/infra"},
        "user": {"username": "alice"},
        "assignees": [{"username": "bob"}, {"username": "carol"}, {"no_name": 1}],
        "labels": [{"title": "bug"}, {"title": "workflow::in progress"}],
    }


def test_normalize_non_issue_returns_none():
    assert normalize({"object_kind": "merge_request"}) is None
    assert normalize({}) is None


def test_normalize_maps_three_actions():
    for gl_action, expected in [("open", "open"), ("reopen", "reopen"), ("close", "close")]:
        ev = normalize(_payload(gl_action))
        assert ev is not None and ev.action == expected


def test_normalize_drops_update():
    assert normalize(_payload("update")) is None     # 'update' is intentionally dropped


def test_normalize_drops_unknown_action():
    assert normalize(_payload("approved")) is None


def test_normalize_extracts_fields():
    ev = normalize(_payload("open"))
    assert ev.kind == "issue"
    assert ev.project == "fakspro/infra"
    assert ev.iid == "7"                              # stringified
    assert ev.title == "Fix it"
    assert ev.url == "https://gl/x/-/issues/7"
    assert ev.state == "opened"
    assert ev.author == "alice"
    assert ev.due == "2026-06-10"


def test_normalize_extracts_assignees_and_labels():
    ev = normalize(_payload("open"))
    assert ev.assignees == ["bob", "carol"]           # entries without username dropped
    assert ev.labels == ["bug", "workflow::in progress"]


def test_normalize_handles_missing_optional_fields():
    payload = {"object_kind": "issue", "object_attributes": {"action": "open"}}
    ev = normalize(payload)
    assert ev is not None
    assert ev.iid == "" and ev.project == "" and ev.author is None
    assert ev.assignees == [] and ev.labels == []
