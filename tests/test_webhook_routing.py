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

    def handle(self, event):
        self.calls.append({
            "room": event.room,
            "action": event.action,
            "watched": bool((event.extra or {}).get("watched")),
        })
        return {"sent": [event.room]}


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
