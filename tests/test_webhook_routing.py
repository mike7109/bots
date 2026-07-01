"""POST /webhook — issue routing: default source room + watched-issue rooms.

Calls the REAL handler (app.webhook) directly (no TestClient/httpx, matching
test_poke). A fake engine records each handle() call's (room, action, watched
flag); a fake Sources.match_path returns a configured room or None. Real
Settings(tmp) holds the webhook secret + watched rules.
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
            "room": event.room,
            "action": event.action,
            "watched": bool((event.extra or {}).get("watched")),
        }
        if event.kind == "note":                     # note-only fields; issue recs stay 3-key
            rec["comment"] = event.comment
            rec["thread_root"] = (event.extra or {}).get("thread_root")
        self.calls.append(rec)
        return {"sent": [event.room], "event_id": f"$evt{self._seq}"}


class FakeStore:
    """In-memory get/set_state — backs note-thread roots in the webhook handler."""
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


def _payload(action="open", labels=("bug",)):
    return {
        "object_kind": "issue",
        "object_attributes": {"iid": 7, "title": "X", "url": "https://gl/7",
                              "state": "opened", "action": action},
        "project": {"path_with_namespace": PROJECT},
        "labels": [{"title": t} for t in labels],
        "assignees": [],
    }


def _ctx(settings, room=DEFAULT_ROOM):
    return FakeCtx(settings, FakeEngine(), FakeSources(room))


def test_open_no_watched_goes_to_source_room(settings, monkeypatch):
    ctx = _ctx(settings)
    monkeypatch.setattr(app_module, "ctx", ctx)
    _call(ctx, _payload("open"))
    assert ctx.engine.calls == [{"room": DEFAULT_ROOM, "action": "open", "watched": False}]


def test_bad_token_rejected(settings, monkeypatch):
    ctx = _ctx(settings)
    monkeypatch.setattr(app_module, "ctx", ctx)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        _call(ctx, _payload("open"), token="wrong")
    assert exc.value.status_code == 403


def test_open_with_watched_tag_posts_default_and_watch(settings, monkeypatch):
    settings.set_watched([{"name": "Sec", "tags": ["security"], "rooms": ["!sec:srv"]}])
    ctx = _ctx(settings)
    monkeypatch.setattr(app_module, "ctx", ctx)
    _call(ctx, _payload("open", labels=("security",)))
    assert ctx.engine.calls == [
        {"room": DEFAULT_ROOM, "action": "open", "watched": False},
        {"room": "!sec:srv", "action": "open", "watched": True},
    ]


def test_update_with_watched_goes_only_to_watch(settings, monkeypatch):
    settings.set_watched([{"name": "Sec", "tags": ["security"], "rooms": ["!sec:srv"]}])
    ctx = _ctx(settings)
    monkeypatch.setattr(app_module, "ctx", ctx)
    _call(ctx, _payload("update", labels=("security",)))
    # update never hits the default room — only the watch room
    assert ctx.engine.calls == [{"room": "!sec:srv", "action": "update", "watched": True}]


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
    assert ctx.engine.calls == [{"room": "!sec:srv", "action": "open", "watched": True}]


def test_watch_room_equal_to_default_not_double_posted(settings, monkeypatch):
    settings.set_watched([{"name": "Sec", "tags": ["security"], "rooms": [DEFAULT_ROOM]}])
    ctx = _ctx(settings)
    monkeypatch.setattr(app_module, "ctx", ctx)
    _call(ctx, _payload("open", labels=("security",)))
    # same room as default -> posted once (as the default, not watched)
    assert ctx.engine.calls == [{"room": DEFAULT_ROOM, "action": "open", "watched": False}]


# --- note (comment) routing: watch-rooms only, throttled ---------------
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
    # never the source room — only the watch room, marked watched, with the comment.
    # First comment has no thread root yet (it becomes the root).
    assert ctx.engine.calls == [
        {"room": "!sec:srv", "action": "create", "watched": True,
         "comment": "готово @m.bahmutskij", "thread_root": None},
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
    # guard defaults: max_per_target=5 -> note cap=4; the 5th comment to the room
    # is shed BEFORE engine.handle (so it never records toward the global breaker).
    settings.set_watched([{"name": "Sec", "tags": ["security"], "rooms": ["!sec:srv"]}])
    ctx = _ctx(settings)
    monkeypatch.setattr(app_module, "ctx", ctx)
    for _ in range(5):
        res = _call(ctx, _note_payload())
    assert len(ctx.engine.calls) == 4                # only 4 delivered
    assert any(k.startswith("throttled:") for k in res["results"])


# --- note threading: comments of one issue nest under the first ---------
def test_note_first_comment_becomes_thread_root(settings, monkeypatch):
    settings.set_watched([{"name": "Sec", "tags": ["security"], "rooms": ["!sec:srv"]}])
    ctx = _ctx(settings)
    monkeypatch.setattr(app_module, "ctx", ctx)
    _call(ctx, _note_payload())
    # posted WITHOUT a thread root, and its event id is stored as the root
    assert ctx.engine.calls[0]["thread_root"] is None
    assert ctx.store.get_state("note_thread", f"{PROJECT}#7@!sec:srv") == {"event_id": "$evt1"}


def test_note_second_comment_threads_under_first(settings, monkeypatch):
    settings.set_watched([{"name": "Sec", "tags": ["security"], "rooms": ["!sec:srv"]}])
    ctx = _ctx(settings)
    monkeypatch.setattr(app_module, "ctx", ctx)
    _call(ctx, _note_payload())                                  # #1 -> root $evt1
    _call(ctx, _note_payload(note="второй коммент"))            # #2 -> nests under $evt1
    assert ctx.engine.calls[0]["thread_root"] is None
    assert ctx.engine.calls[1]["thread_root"] == "$evt1"        # threaded under the first
    # root is not overwritten by later comments
    assert ctx.store.get_state("note_thread", f"{PROJECT}#7@!sec:srv") == {"event_id": "$evt1"}


def test_note_threads_are_per_room(settings, monkeypatch):
    # same issue watched into two rooms keeps an independent thread root per room
    settings.set_watched([{"name": "Sec", "tags": ["security"], "rooms": ["!a:srv", "!b:srv"]}])
    ctx = _ctx(settings)
    monkeypatch.setattr(app_module, "ctx", ctx)
    _call(ctx, _note_payload())
    assert ctx.store.get_state("note_thread", f"{PROJECT}#7@!a:srv") == {"event_id": "$evt1"}
    assert ctx.store.get_state("note_thread", f"{PROJECT}#7@!b:srv") == {"event_id": "$evt2"}


def test_note_store_fault_never_breaks_delivery(settings, monkeypatch):
    # A SQLite/JSON hiccup in the thread store must degrade to "no threading",
    # NOT bubble up as a 500 that drops (or on resend, duplicates) the comment.
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
    assert ctx.engine.calls == [                            # still delivered, just un-threaded
        {"room": "!sec:srv", "action": "create", "watched": True,
         "comment": "готово @m.bahmutskij", "thread_root": None},
    ]


def test_note_without_iid_posts_top_level(settings, monkeypatch):
    # A malformed (iid-less) note must post a normal message, never share a
    # per-project-room thread key with other iid-less notes.
    settings.set_watched([{"name": "Sec", "tags": ["security"], "rooms": ["!sec:srv"]}])
    ctx = _ctx(settings)
    monkeypatch.setattr(app_module, "ctx", ctx)
    _call(ctx, _note_payload(iid=None))
    assert ctx.engine.calls[0]["thread_root"] is None
    assert ctx.store.d == {}                                # no thread key persisted
