"""POST /webhook — issue routing: default source room + watched-issue threads.

Calls the REAL handler (app.webhook) directly (no TestClient/httpx, matching
test_poke). A fake engine records each handle() call's (kind, room, action,
watched flag); a fake Sources.match_path returns a configured room or None. Real
Settings(tmp) holds the webhook secret + watched rules.

A watched issue is ONE Matrix thread: its ROOT is the full card (issue_root),
created once per (issue, room); close/reopen/comments/assignee+due changes nest
under it. `open` posts only the root; label/rename-only updates post nothing.
"""
from __future__ import annotations

import os

os.environ.setdefault("STATE_DB", "/tmp/webhook-import-state.db")

import asyncio                                   # noqa: E402
import json as _jsonlib                          # noqa: E402

import pytest                                    # noqa: E402
from starlette.requests import Request           # noqa: E402

from botkit.store import Store                   # noqa: E402

import app as app_module                         # noqa: E402
from settings import Settings                    # noqa: E402

SECRET = "wh-secret"
PROJECT = "fakspro/infra"
DEFAULT_ROOM = "!default:srv"


class FakeEngine:
    def __init__(self):
        self.calls = []
        self._seq = 0

    def handle(self, event):
        self._seq += 1
        rec = {
            "kind": event.kind,
            "room": event.room,
            "action": event.action,
            "watched": bool((event.extra or {}).get("watched")),
        }
        if event.kind in ("note", "issue_change", "issue_card"):   # threaded content carries a root
            rec["thread_root"] = (event.extra or {}).get("thread_root")
        if event.kind == "note":
            rec["comment"] = event.comment
        self.calls.append(rec)
        return {"sent": [event.room], "event_id": f"$evt{self._seq}"}


class FakeStore:
    """In-memory get/set_state — backs issue-thread roots in the webhook handler."""
    def __init__(self):
        self.d = {}

    def get_state(self, kind, key):
        return self.d.get((kind, key))

    def set_state(self, kind, key, value):
        self.d[(kind, key)] = value


class FakeSources:
    def __init__(self, room=DEFAULT_ROOM):
        self._room = room

    def match_path(self, project):
        return {"room": self._room} if self._room else None


class FakeCtx:
    def __init__(self, settings, engine, sources):
        self.settings = settings
        self.engine = engine
        self.sources = sources
        self.store = FakeStore()


@pytest.fixture
def settings(tmp_path):
    s = Settings(Store(path=str(tmp_path / "state.db")))
    s.update_conn({"webhook_secret": SECRET})
    return s


def _call(ctx, payload, token=SECRET):
    body = _jsonlib.dumps(payload).encode()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    req = Request({"type": "http", "method": "POST", "path": "/webhook",
                   "headers": [], "query_string": b""}, receive)
    return asyncio.run(app_module.webhook(req, x_gitlab_token=token))


def _payload(action="open", labels=("bug",), changes=None):
    attr = {"iid": 7, "title": "X", "url": "https://gl/7",
            "state": "opened", "action": action}
    p = {
        "object_kind": "issue",
        "object_attributes": attr,
        "project": {"path_with_namespace": PROJECT},
        "labels": [{"title": t} for t in labels],
        "assignees": [],
    }
    if changes is not None:
        p["changes"] = changes
    return p


def _ctx(settings, room=DEFAULT_ROOM):
    return FakeCtx(settings, FakeEngine(), FakeSources(room))


def test_open_no_watched_goes_to_source_room(settings, monkeypatch):
    ctx = _ctx(settings)
    monkeypatch.setattr(app_module, "ctx", ctx)
    _call(ctx, _payload("open"))
    assert ctx.engine.calls == [
        {"kind": "issue", "room": DEFAULT_ROOM, "action": "open", "watched": False}]


def test_bad_token_rejected(settings, monkeypatch):
    ctx = _ctx(settings)
    monkeypatch.setattr(app_module, "ctx", ctx)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        _call(ctx, _payload("open"), token="wrong")
    assert exc.value.status_code == 403


def test_open_with_watched_tag_posts_default_and_root_card(settings, monkeypatch):
    settings.set_watched([{"name": "Sec", "tags": ["security"], "rooms": ["!sec:srv"]}])
    ctx = _ctx(settings)
    monkeypatch.setattr(app_module, "ctx", ctx)
    _call(ctx, _payload("open", labels=("security",)))
    # source room: standalone open; watch room: the ANCHOR (thread root/title) + the
    # full CARD nested under it as the first in-thread message (topic visible at once).
    # `open` adds nothing further (the card is the open notice).
    assert ctx.engine.calls == [
        {"kind": "issue", "room": DEFAULT_ROOM, "action": "open", "watched": False},
        {"kind": "issue_root", "room": "!sec:srv", "action": "open", "watched": True},
        {"kind": "issue_card", "room": "!sec:srv", "action": "open", "watched": True,
         "thread_root": "$evt2"},
    ]
    assert ctx.store.get_state("issue_thread", f"{PROJECT}#7@!sec:srv") == {"event_id": "$evt2"}


def test_close_on_watched_nests_under_root(settings, monkeypatch):
    # close makes the ROOT lazily (this room saw no `open`) then nests the state change.
    settings.set_watched([{"name": "Sec", "tags": ["security"], "rooms": ["!sec:srv"]}])
    ctx = _ctx(settings)
    monkeypatch.setattr(app_module, "ctx", ctx)
    _call(ctx, _payload("close", labels=("security",)))
    assert ctx.engine.calls == [
        {"kind": "issue", "room": DEFAULT_ROOM, "action": "close", "watched": False},
        {"kind": "issue_root", "room": "!sec:srv", "action": "close", "watched": True},       # anchor
        {"kind": "issue_card", "room": "!sec:srv", "action": "close", "watched": True,
         "thread_root": "$evt2"},                                                             # card
        {"kind": "issue", "room": "!sec:srv", "action": "close", "watched": True},            # nested state change
    ]


def test_update_label_only_makes_root_but_posts_nothing_else(settings, monkeypatch):
    # a watched-label add / label change fires `update` with no assignee/due diff:
    # it lazily creates the thread root but posts NO change message.
    settings.set_watched([{"name": "Sec", "tags": ["security"], "rooms": ["!sec:srv"]}])
    ctx = _ctx(settings)
    monkeypatch.setattr(app_module, "ctx", ctx)
    _call(ctx, _payload("update", labels=("security",)))
    # lazily creates the topic (anchor + card), nothing more for a label-only edit
    assert ctx.engine.calls == [
        {"kind": "issue_root", "room": "!sec:srv", "action": "update", "watched": True},
        {"kind": "issue_card", "room": "!sec:srv", "action": "update", "watched": True,
         "thread_root": "$evt1"},
    ]


def test_update_assignee_change_posts_issue_change(settings, monkeypatch):
    settings.set_watched([{"name": "Sec", "tags": ["security"], "rooms": ["!sec:srv"]}])
    ctx = _ctx(settings)
    monkeypatch.setattr(app_module, "ctx", ctx)
    changes = {"assignees": {"previous": [], "current": [{"username": "bob"}]}}
    _call(ctx, _payload("update", labels=("security",), changes=changes))
    assert ctx.engine.calls == [
        {"kind": "issue_root", "room": "!sec:srv", "action": "update", "watched": True},       # anchor
        {"kind": "issue_card", "room": "!sec:srv", "action": "update", "watched": True,
         "thread_root": "$evt1"},                                                              # card
        {"kind": "issue_change", "room": "!sec:srv", "action": "update",
         "watched": True, "thread_root": "$evt1"},                                             # the change
    ]


def test_update_due_change_posts_issue_change(settings, monkeypatch):
    settings.set_watched([{"name": "Sec", "tags": ["security"], "rooms": ["!sec:srv"]}])
    ctx = _ctx(settings)
    monkeypatch.setattr(app_module, "ctx", ctx)
    changes = {"due_date": {"previous": None, "current": "2026-07-10"}}
    _call(ctx, _payload("update", labels=("security",), changes=changes))
    kinds = [c["kind"] for c in ctx.engine.calls]
    assert kinds == ["issue_root", "issue_card", "issue_change"]


def test_update_without_watched_is_ignored(settings, monkeypatch):
    ctx = _ctx(settings)
    monkeypatch.setattr(app_module, "ctx", ctx)
    res = _call(ctx, _payload("update", labels=("bug",)))
    assert ctx.engine.calls == []
    assert "ignored" in res


def test_watched_works_without_source(settings, monkeypatch):
    settings.set_watched([{"name": "Sec", "tags": ["security"], "rooms": ["!sec:srv"]}])
    ctx = _ctx(settings, room=None)            # no source match for this project
    monkeypatch.setattr(app_module, "ctx", ctx)
    _call(ctx, _payload("open", labels=("security",)))
    assert ctx.engine.calls == [
        {"kind": "issue_root", "room": "!sec:srv", "action": "open", "watched": True},
        {"kind": "issue_card", "room": "!sec:srv", "action": "open", "watched": True,
         "thread_root": "$evt1"},
    ]


def test_watch_room_equal_to_default_threads_not_flat(settings, monkeypatch):
    # When the source room IS the watch room, a watched open must build the TOPIC
    # there (anchor + card), NOT a flat standalone message — and not both.
    settings.set_watched([{"name": "Sec", "tags": ["security"], "rooms": [DEFAULT_ROOM]}])
    ctx = _ctx(settings)
    monkeypatch.setattr(app_module, "ctx", ctx)
    _call(ctx, _payload("open", labels=("security",)))
    assert ctx.engine.calls == [
        {"kind": "issue_root", "room": DEFAULT_ROOM, "action": "open", "watched": True},
        {"kind": "issue_card", "room": DEFAULT_ROOM, "action": "open", "watched": True,
         "thread_root": "$evt1"},
    ]


# --- note (comment) routing: watch-rooms only, threaded, throttled ----------
def _note_payload(labels=("security",), note="готово @m.bahmutskij",
                  action="create", system=False, noteable="Issue", iid=7):
    issue = {"title": "X", "labels": [{"title": t} for t in labels]}
    if iid is not None:
        issue["iid"] = iid
    return {
        "object_kind": "note",
        "object_attributes": {"noteable_type": noteable, "action": action,
                              "system": system, "note": note,
                              "url": "https://gl/7#note_1"},
        "issue": issue,
        "project": {"path_with_namespace": PROJECT},
        "user": {"username": "d.nikulin"},
    }


def test_note_on_watched_goes_only_to_watch_room(settings, monkeypatch):
    settings.set_watched([{"name": "Sec", "tags": ["security"], "rooms": ["!sec:srv"]}])
    ctx = _ctx(settings)                              # a real source room exists
    monkeypatch.setattr(app_module, "ctx", ctx)
    _call(ctx, _note_payload())
    # never the source room: anchor + card create the topic, then the comment nests
    # under the anchor — all only in the watch room.
    assert ctx.engine.calls == [
        {"kind": "issue_root", "room": "!sec:srv", "action": "create", "watched": True},
        {"kind": "issue_card", "room": "!sec:srv", "action": "create", "watched": True,
         "thread_root": "$evt1"},
        {"kind": "note", "room": "!sec:srv", "action": "create", "watched": True,
         "comment": "готово @m.bahmutskij", "thread_root": "$evt1"},
    ]


def test_note_without_watched_match_ignored(settings, monkeypatch):
    ctx = _ctx(settings)
    monkeypatch.setattr(app_module, "ctx", ctx)
    res = _call(ctx, _note_payload(labels=("bug",)))
    assert ctx.engine.calls == []
    assert "ignored" in res and "no watched match" in res["ignored"]


def test_note_system_and_edit_are_unhandled(settings, monkeypatch):
    ctx = _ctx(settings)
    monkeypatch.setattr(app_module, "ctx", ctx)
    for payload in (_note_payload(system=True), _note_payload(action="update")):
        res = _call(ctx, payload)
        assert res == {"ignored": "unhandled event: note"}
    assert ctx.engine.calls == []


def test_note_burst_is_throttled_before_the_breaker(settings, monkeypatch):
    # guard defaults: max_per_target=5 -> body cap=4; the 5th comment to the room
    # is shed BEFORE engine.handle (so it never records toward the global breaker).
    # The root card is exempt (created once) and doesn't consume the throttle.
    settings.set_watched([{"name": "Sec", "tags": ["security"], "rooms": ["!sec:srv"]}])
    ctx = _ctx(settings)
    monkeypatch.setattr(app_module, "ctx", ctx)
    for _ in range(5):
        res = _call(ctx, _note_payload())
    comment_calls = [c for c in ctx.engine.calls if "comment" in c]
    assert len(comment_calls) == 4                   # only 4 comments delivered (5th shed)
    assert any(k.startswith("throttled:") for k in res["results"])


# --- issue threading: the root card anchors; everything nests under it ------
def test_note_first_comment_is_threaded_under_the_root(settings, monkeypatch):
    settings.set_watched([{"name": "Sec", "tags": ["security"], "rooms": ["!sec:srv"]}])
    ctx = _ctx(settings)
    monkeypatch.setattr(app_module, "ctx", ctx)
    _call(ctx, _note_payload())
    assert ctx.engine.calls[0]["kind"] == "issue_root"           # anchor (thread root)
    assert ctx.engine.calls[1]["kind"] == "issue_card"           # card nested first
    comment = next(c for c in ctx.engine.calls if "comment" in c)
    assert comment["thread_root"] == "$evt1"                     # comment nests under the anchor
    assert ctx.store.get_state("issue_thread", f"{PROJECT}#7@!sec:srv") == {"event_id": "$evt1"}


def test_note_second_comment_reuses_the_same_thread(settings, monkeypatch):
    settings.set_watched([{"name": "Sec", "tags": ["security"], "rooms": ["!sec:srv"]}])
    ctx = _ctx(settings)
    monkeypatch.setattr(app_module, "ctx", ctx)
    _call(ctx, _note_payload())                                  # root $evt1 + comment
    _call(ctx, _note_payload(note="второй коммент"))            # comment only, same thread
    comment_calls = [c for c in ctx.engine.calls if "comment" in c]
    assert [c["thread_root"] for c in comment_calls] == ["$evt1", "$evt1"]
    roots = [c for c in ctx.engine.calls if c["kind"] == "issue_root"]
    assert len(roots) == 1                                       # root posted once only
    assert ctx.store.get_state("issue_thread", f"{PROJECT}#7@!sec:srv") == {"event_id": "$evt1"}


def test_note_threads_are_per_room(settings, monkeypatch):
    # same issue watched into two rooms keeps an independent thread (root) per room
    settings.set_watched([{"name": "Sec", "tags": ["security"], "rooms": ["!a:srv", "!b:srv"]}])
    ctx = _ctx(settings)
    monkeypatch.setattr(app_module, "ctx", ctx)
    _call(ctx, _note_payload())               # !a: anchor $evt1 + card $evt2 + comment $evt3
    assert ctx.store.get_state("issue_thread", f"{PROJECT}#7@!a:srv") == {"event_id": "$evt1"}
    assert ctx.store.get_state("issue_thread", f"{PROJECT}#7@!b:srv") == {"event_id": "$evt4"}


def test_note_store_fault_never_breaks_delivery(settings, monkeypatch):
    # A SQLite/JSON hiccup in the thread store must NOT bubble up as a 500 that
    # drops (or on resend, duplicates) the comment — delivery still happens.
    settings.set_watched([{"name": "Sec", "tags": ["security"], "rooms": ["!sec:srv"]}])
    ctx = _ctx(settings)

    class BoomStore:
        def get_state(self, *a):
            raise RuntimeError("db down")

        def set_state(self, *a, **k):
            raise RuntimeError("db down")

    ctx.store = BoomStore()
    monkeypatch.setattr(app_module, "ctx", ctx)
    res = _call(ctx, _note_payload())                       # must NOT raise
    assert "posted" in res
    comment_calls = [c for c in ctx.engine.calls if "comment" in c]
    assert len(comment_calls) == 1                         # comment still delivered, no crash
    assert comment_calls[0]["comment"] == "готово @m.bahmutskij"


def test_note_without_iid_posts_top_level(settings, monkeypatch):
    # A malformed (iid-less) note must post a normal message (no root, no thread),
    # never share a per-project-room thread key with other iid-less notes.
    settings.set_watched([{"name": "Sec", "tags": ["security"], "rooms": ["!sec:srv"]}])
    ctx = _ctx(settings)
    monkeypatch.setattr(app_module, "ctx", ctx)
    _call(ctx, _note_payload(iid=None))
    assert ctx.engine.calls == [                            # only the comment, top-level
        {"kind": "note", "room": "!sec:srv", "action": "create", "watched": True,
         "comment": "готово @m.bahmutskij", "thread_root": None},
    ]
    assert ctx.store.d == {}                                # no thread key persisted
