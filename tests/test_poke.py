"""POST /api/poke — issue-graph asks the bot to DM-remind ("пнуть") an assignee.

Calls the REAL handler (app.poke) directly — no TestClient/httpx, since CI installs
a minimal env without httpx. Swaps app.ctx for a fake context: a fake Matrix
(records get_or_create_dm + send_html, can raise), a real Identity / Settings /
Store(tmp), and a real SendGuard whose `blocked()` we can force. Importing app.py
runs its module-level build_context() once (kept off the network by a tmp STATE_DB
set before import + the fake ctx swapped in per test).
"""
from __future__ import annotations

import os

import pytest

# Point the default Store at a throwaway DB BEFORE importing app (its module-level
# build_context() opens a Store). The fake ctx below uses its own tmp store anyway.
os.environ.setdefault("STATE_DB", "/tmp/poke-import-state.db")

import asyncio                                  # noqa: E402
import json as _jsonlib                          # noqa: E402

from starlette.requests import Request           # noqa: E402

from botkit.identity import Identity            # noqa: E402
from botkit.notify.guard import SendGuard       # noqa: E402
from botkit.store import Store                  # noqa: E402

import app as app_module                        # noqa: E402
from settings import Settings                   # noqa: E402

TOKEN = "s3cret-poke-token"
USERS = {
    "m.bahmutskij": {"matrix": "@m.bahmutskij:fakspro.ru", "name": "Михаил"},
    # "ghost" is intentionally absent -> unmapped (no matrix_domain set either).
}
ISSUE = {"iid": 5, "title": "Кнопка оплаты", "web_url": "http://gl.local/a/b/-/issues/5",
         "project_path": "a/b"}


class FakeMatrix:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.dm_calls: list[str] = []
        self.sends: list[dict] = []

    def get_or_create_dm(self, user_id):
        self.dm_calls.append(user_id)
        if self.fail:
            raise RuntimeError("homeserver down")
        return f"!dm-{user_id}"

    def send_html(self, room, html, *, mention_user_ids=None, notice=True, **kw):
        self.sends.append({"room": room, "html": html,
                           "mentions": list(mention_user_ids or []), "notice": notice})
        return "$evt"


class FakeCtx:
    def __init__(self, store, settings, identity, matrix, guard):
        self.store = store
        self.settings = settings
        self.identity = identity
        self.matrix = matrix
        self.guard = guard


def _guard(store, *, blocked=False):
    g = SendGuard(
        enabled=True, window_s=600, max_global=50, target_window_s=300,
        max_per_target=5, max_duplicate=3, auto_reset_s=0,
        get_tripped=lambda: {"tripped": False}, set_tripped=lambda s: None,
        on_trip=lambda r: None,
    )
    if blocked:
        # Force blocked() True without a real trip.
        g.blocked = lambda: True   # type: ignore[method-assign]
    return g


@pytest.fixture
def store(tmp_path):
    return Store(path=str(tmp_path / "state.db"))


@pytest.fixture
def settings(store):
    s = Settings(store)
    s.update_conn({"poke_token": TOKEN})   # token configured -> feature ON
    return s


@pytest.fixture
def identity():
    return Identity(USERS)


class _Resp:
    """Minimal response shim (.status_code / .json()) over the handler's JSONResponse."""
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _DirectClient:
    """Calls the real app.poke coroutine directly — no TestClient/httpx dependency.
    Builds a starlette Request from the JSON body and passes Authorization straight
    to the handler's arg (FastAPI's Header injection isn't involved when called raw)."""
    def post(self, path, json=None, headers=None):
        authorization = (headers or {}).get("Authorization", "")
        body = _jsonlib.dumps(json if json is not None else {}).encode()

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        req = Request({"type": "http", "method": "POST", "path": path,
                       "headers": [], "query_string": b""}, receive)
        resp = asyncio.run(app_module.poke(req, authorization=authorization))
        return _Resp(resp.status_code, _jsonlib.loads(bytes(resp.body)))


def _client(store, settings, identity, matrix, guard, monkeypatch):
    """Swap app.ctx for the fake; return a direct (TestClient-free) caller."""
    monkeypatch.setattr(app_module, "ctx", FakeCtx(store, settings, identity, matrix, guard))
    return _DirectClient()


def _auth(token=TOKEN):
    return {"Authorization": f"Bearer {token}"}


def _body(**kw):
    base = {"gitlab_username": "m.bahmutskij", "issue": dict(ISSUE), "message": None}
    base.update(kw)
    return base


# --- token (fail-closed) ----------------------------------------------
def test_no_token_configured_401(store, identity, monkeypatch):
    settings = Settings(store)                  # no poke_token set -> feature OFF
    mx = FakeMatrix()
    client = _client(store, settings, identity, mx, _guard(store), monkeypatch)
    r = client.post("/api/poke", json=_body(), headers=_auth())
    assert r.status_code == 401
    assert "POKE_TOKEN" in r.json()["reason"]
    assert mx.sends == []


def test_missing_bearer_401(store, settings, identity, monkeypatch):
    mx = FakeMatrix()
    client = _client(store, settings, identity, mx, _guard(store), monkeypatch)
    r = client.post("/api/poke", json=_body())   # no Authorization header
    assert r.status_code == 401 and r.json()["reason"] == "неверный токен"
    assert mx.sends == []


def test_wrong_token_401(store, settings, identity, monkeypatch):
    mx = FakeMatrix()
    client = _client(store, settings, identity, mx, _guard(store), monkeypatch)
    r = client.post("/api/poke", json=_body(), headers=_auth("nope"))
    assert r.status_code == 401 and r.json()["reason"] == "неверный токен"
    assert mx.sends == []


# --- body validation --------------------------------------------------
def test_missing_fields_400(store, settings, identity, monkeypatch):
    mx = FakeMatrix()
    client = _client(store, settings, identity, mx, _guard(store), monkeypatch)
    r = client.post("/api/poke", json={"gitlab_username": "m.bahmutskij"}, headers=_auth())
    assert r.status_code == 400
    assert mx.sends == []


# --- kill switch ------------------------------------------------------
def test_kill_switch_503(store, settings, identity, monkeypatch):
    settings.update_global({"enabled": False})
    mx = FakeMatrix()
    client = _client(store, settings, identity, mx, _guard(store), monkeypatch)
    r = client.post("/api/poke", json=_body(), headers=_auth())
    assert r.status_code == 503 and r.json()["reason"] == "бот выключен"
    assert mx.sends == []


# --- recipient resolution ---------------------------------------------
def test_unknown_user_422_user_not_mapped(store, settings, identity, monkeypatch):
    mx = FakeMatrix()
    client = _client(store, settings, identity, mx, _guard(store), monkeypatch)
    r = client.post("/api/poke", json=_body(gitlab_username="ghost"), headers=_auth())
    assert r.status_code == 422
    j = r.json()
    assert j["reason"] == "user_not_mapped"          # machine token per the spec
    assert "ghost" in j["detail"]                    # human detail
    assert mx.sends == []


# --- happy path -------------------------------------------------------
def test_success_delivers_and_records(store, settings, identity, monkeypatch):
    mx = FakeMatrix()
    guard = _guard(store)
    recorded = []
    guard.record = lambda tk, content: recorded.append((tk, content))  # type: ignore[method-assign]
    client = _client(store, settings, identity, mx, guard, monkeypatch)

    r = client.post("/api/poke", json=_body(), headers=_auth())
    assert r.status_code == 200 and r.json() == {"ok": True, "delivered": True}

    # DM sent to the resolved mxid, not a room; pinged, notice off.
    assert mx.dm_calls == ["@m.bahmutskij:fakspro.ru"]
    assert len(mx.sends) == 1
    s = mx.sends[0]
    assert s["room"] == "!dm-@m.bahmutskij:fakspro.ru"
    assert s["mentions"] == ["@m.bahmutskij:fakspro.ru"] and s["notice"] is False
    assert "#5" in s["html"] and "Кнопка оплаты" in s["html"]

    # guard.record fired (counts toward the global breaker).
    assert recorded and recorded[0][0] == "dm:@m.bahmutskij:fakspro.ru"

    # cooldown state + per-user rate persisted.
    assert store.get_state("poke", "m.bahmutskij:5")["ts"] > 0
    assert len(store.get_state("poke_rate", "m.bahmutskij")["ts"]) == 1

    # log_event("poke", ..., "sent") written.
    rows = store.recent_log(10)
    assert any(row["kind"] == "poke" and row["status"] == "sent" for row in rows)


# --- anti-spam: per-(user,issue) cooldown -----------------------------
def test_cooldown_second_poke_429_no_send(store, settings, identity, monkeypatch):
    mx = FakeMatrix()
    client = _client(store, settings, identity, mx, _guard(store), monkeypatch)
    assert client.post("/api/poke", json=_body(), headers=_auth()).status_code == 200
    assert len(mx.sends) == 1
    r = client.post("/api/poke", json=_body(), headers=_auth())   # same user+issue
    assert r.status_code == 429
    assert len(mx.sends) == 1                         # NO new send


# --- anti-spam: per-user rate cap -------------------------------------
def test_per_user_cap_429(store, settings, identity, monkeypatch):
    mx = FakeMatrix()
    client = _client(store, settings, identity, mx, _guard(store), monkeypatch)
    # per_user_max=4: four DIFFERENT issues deliver, the fifth trips the cap.
    for iid in (1, 2, 3, 4):
        body = _body(issue={**ISSUE, "iid": iid})
        assert client.post("/api/poke", json=body, headers=_auth()).status_code == 200
    assert len(mx.sends) == 4
    r = client.post("/api/poke", json=_body(issue={**ISSUE, "iid": 9}), headers=_auth())
    assert r.status_code == 429
    assert len(mx.sends) == 4                         # capped -> no fifth send


def test_per_user_cap_below_breaker_per_target(store, settings):
    # The per-user cap MUST stay below the SendGuard per-target cap so a legit
    # poke can never be the message that trips the global breaker.
    assert settings.get_poke()["per_user_max"] < settings.get_guard()["max_per_target"]


# --- breaker backstop -------------------------------------------------
def test_breaker_blocked_503_no_send(store, settings, identity, monkeypatch):
    mx = FakeMatrix()
    client = _client(store, settings, identity, mx, _guard(store, blocked=True), monkeypatch)
    r = client.post("/api/poke", json=_body(), headers=_auth())
    assert r.status_code == 503 and "предохранитель" in r.json()["reason"]
    assert mx.sends == []


# --- delivery failure -------------------------------------------------
def test_delivery_failure_502_logs_error(store, settings, identity, monkeypatch):
    mx = FakeMatrix(fail=True)
    client = _client(store, settings, identity, mx, _guard(store), monkeypatch)
    r = client.post("/api/poke", json=_body(), headers=_auth())
    assert r.status_code == 502 and "ошибка доставки" in r.json()["reason"]
    rows = store.recent_log(10)
    assert any(row["kind"] == "poke" and row["status"] == "error" for row in rows)
    # No cooldown burned on failure (so issue-graph can retry).
    assert store.get_state("poke", "m.bahmutskij:5") is None


# --- escaping ---------------------------------------------------------
def test_custom_message_is_escaped(store, settings, identity, monkeypatch):
    mx = FakeMatrix()
    client = _client(store, settings, identity, mx, _guard(store), monkeypatch)
    body = _body(message="<script>alert(1)</script>\nline2")
    assert client.post("/api/poke", json=body, headers=_auth()).status_code == 200
    html = mx.sends[0]["html"]
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "<br>" in html                             # newline preserved as <br>


def test_default_message_escapes_issue_title(store, settings, identity, monkeypatch):
    mx = FakeMatrix()
    client = _client(store, settings, identity, mx, _guard(store), monkeypatch)
    body = _body(issue={**ISSUE, "title": "<img src=x onerror=alert(1)>"})
    assert client.post("/api/poke", json=body, headers=_auth()).status_code == 200
    html = mx.sends[0]["html"]
    assert "<img" not in html and "&lt;img" in html


def test_default_message_neutralises_hostile_url(store, settings, identity, monkeypatch):
    mx = FakeMatrix()
    client = _client(store, settings, identity, mx, _guard(store), monkeypatch)
    body = _body(issue={**ISSUE, "web_url": "javascript:alert(1)"})
    assert client.post("/api/poke", json=body, headers=_auth()).status_code == 200
    html = mx.sends[0]["html"]
    assert "javascript:" not in html.split('href="')[1].split('"')[0]
