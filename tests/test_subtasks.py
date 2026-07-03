"""subtasks.py — the child-Task GraphQL poll for watched issues.

The poll enumerates issue_thread keys, fetches children per project (the fetch
is stubbed here), diffs against the "subtasks" snapshot and posts transitions
into the issue's thread. Contracts under test: silent baseline seeding, the
open/close/reopen diff vocabulary, silence for deletions, per-room fan-out
under each room's OWN thread root, closed-parent scope pruning + reopen reset,
throttle shedding, rule gating and poll-throttle timing.
"""
from __future__ import annotations

import datetime as dt
import os
import pathlib

os.environ.setdefault("STATE_DB", "/tmp/subtasks-import-state.db")

import pytest                                    # noqa: E402

from botkit.store import Store                   # noqa: E402

import subtasks                                  # noqa: E402
from settings import Settings                    # noqa: E402

PROJECT = "fakspro/infra"
ROOM = "!sec:srv"


class FakeEngine:
    def __init__(self):
        self.calls = []

    def handle(self, event):
        self.calls.append({"kind": event.kind, "action": event.action, "iid": event.iid,
                           "title": event.title, "room": event.room, "url": event.url,
                           "thread_root": (event.extra or {}).get("thread_root")})
        return {"sent": [event.room], "event_id": f"$s{len(self.calls)}"}


class FakeSources:
    def match_path(self, project):
        return {"token": "tok"}


class FakeCtx:
    def __init__(self, settings):
        self.settings = settings
        self.engine = FakeEngine()
        self.sources = FakeSources()
        self.store = settings.store
        self.guard = None
        self.config = {"rules": [{"event": "subtask", "actions": ["open", "close", "reopen"]}]}


@pytest.fixture
def ctx(tmp_path):
    s = Settings(Store(path=str(tmp_path / "state.db")))
    s.update_conn({"gitlab_url": "https://gl"})
    subtasks._reset_poll_state()
    return FakeCtx(s)


def _thread(ctx, iid="7", room=ROOM, root="$root"):
    ctx.store.set_state("issue_thread", f"{PROJECT}#{iid}@{room}", {"event_id": root})


def _stub_fetch(monkeypatch, payload):
    """payload: {iid: {"state":..., "tasks": {...}}}; records requested iids."""
    calls = []

    def fetch(gl, project, iids):
        calls.append((project, sorted(iids)))
        return payload
    monkeypatch.setattr(subtasks, "_fetch", fetch)
    return calls


def test_first_poll_seeds_baseline_silently(ctx, monkeypatch):
    _thread(ctx)
    _stub_fetch(monkeypatch, {"7": {"state": "open", "tasks": {
        "12": {"title": "старая", "state": "open"}}}})
    res = subtasks.poll(ctx)
    assert res == {"issues": 1, "seeded": 1, "posted": 0}
    assert ctx.engine.calls == []                       # existing tasks never replay
    assert ctx.store.get_state("subtasks", f"{PROJECT}#7")["tasks"] == {
        "12": {"title": "старая", "state": "open"}}


def test_new_task_posts_open_into_the_thread(ctx, monkeypatch):
    _thread(ctx)
    ctx.store.set_state("subtasks", f"{PROJECT}#7", {"tasks": {}})
    _stub_fetch(monkeypatch, {"7": {"state": "open", "tasks": {
        "12": {"title": "Новая карточка", "state": "open"}}}})
    res = subtasks.poll(ctx)
    assert res["posted"] == 1
    assert ctx.engine.calls == [{
        "kind": "subtask", "action": "open", "iid": "12", "title": "Новая карточка",
        "room": ROOM, "url": f"https://gl/{PROJECT}/-/work_items/12",
        "thread_root": "$root"}]


def test_close_and_reopen_transitions(ctx, monkeypatch):
    _thread(ctx)
    ctx.store.set_state("subtasks", f"{PROJECT}#7", {"tasks": {
        "12": {"title": "a", "state": "open"},
        "13": {"title": "b", "state": "closed"}}})
    _stub_fetch(monkeypatch, {"7": {"state": "open", "tasks": {
        "12": {"title": "a", "state": "closed"},
        "13": {"title": "b", "state": "open"}}}})
    subtasks.poll(ctx)
    assert [(c["action"], c["iid"]) for c in ctx.engine.calls] == [
        ("close", "12"), ("reopen", "13")]


def test_deleted_task_is_silent_and_dropped_from_snapshot(ctx, monkeypatch):
    _thread(ctx)
    ctx.store.set_state("subtasks", f"{PROJECT}#7", {"tasks": {
        "12": {"title": "a", "state": "open"}}})
    _stub_fetch(monkeypatch, {"7": {"state": "open", "tasks": {}}})
    res = subtasks.poll(ctx)
    assert res["posted"] == 0 and ctx.engine.calls == []
    assert ctx.store.get_state("subtasks", f"{PROJECT}#7")["tasks"] == {}


def test_multi_room_fanout_uses_each_rooms_own_root(ctx, monkeypatch):
    _thread(ctx, room="!a:srv", root="$ra")
    _thread(ctx, room="!b:srv", root="$rb")
    ctx.store.set_state("subtasks", f"{PROJECT}#7", {"tasks": {}})
    _stub_fetch(monkeypatch, {"7": {"state": "open", "tasks": {
        "12": {"title": "x", "state": "open"}}}})
    subtasks.poll(ctx)
    assert sorted((c["room"], c["thread_root"]) for c in ctx.engine.calls) == [
        ("!a:srv", "$ra"), ("!b:srv", "$rb")]


def test_closed_parent_leaves_scope_until_reopen(ctx, monkeypatch):
    _thread(ctx)
    ctx.store.set_state("subtasks", f"{PROJECT}#7", {"tasks": {}})
    calls = _stub_fetch(monkeypatch, {"7": {"state": "closed", "tasks": {}}})
    subtasks.poll(ctx)
    assert ctx.store.get_state("subtasks", f"{PROJECT}#7")["parent_closed"] is True
    subtasks.poll(ctx)                                   # second poll: out of scope
    assert len(calls) == 1                               # no second fetch
    # the reopen webhook resets the flag -> polled again
    subtasks.parent_reopened(ctx.store, f"{PROJECT}#7")
    assert "parent_closed" not in ctx.store.get_state("subtasks", f"{PROJECT}#7")
    subtasks.poll(ctx)
    assert len(calls) == 2


def test_burst_is_shed_by_the_note_throttle(ctx, monkeypatch):
    # guard defaults: max_per_target=5 -> cap 4 sends per room window; a bulk
    # task import posts 4 and sheds the rest BEFORE engine.handle.
    _thread(ctx)
    ctx.store.set_state("subtasks", f"{PROJECT}#7", {"tasks": {}})
    tasks = {str(i): {"title": f"t{i}", "state": "open"} for i in range(10, 16)}
    _stub_fetch(monkeypatch, {"7": {"state": "open", "tasks": tasks}})
    res = subtasks.poll(ctx)
    assert len(ctx.engine.calls) == 4 and res["posted"] == 4
    # snapshot still advanced: the shed tasks won't re-post next poll
    assert len(ctx.store.get_state("subtasks", f"{PROJECT}#7")["tasks"]) == 6


def test_no_thread_root_means_nothing_posted(ctx, monkeypatch):
    # thread key exists but the root record vanished -> nothing to nest under
    ctx.store.set_state("issue_thread", f"{PROJECT}#7@{ROOM}", {})
    ctx.store.set_state("subtasks", f"{PROJECT}#7", {"tasks": {}})
    _stub_fetch(monkeypatch, {"7": {"state": "open", "tasks": {
        "12": {"title": "x", "state": "open"}}}})
    assert subtasks.poll(ctx)["posted"] == 0
    assert ctx.engine.calls == []


def test_fetch_failure_never_raises_and_keeps_snapshot(ctx, monkeypatch):
    _thread(ctx)
    ctx.store.set_state("subtasks", f"{PROJECT}#7", {"tasks": {"12": {"title": "a", "state": "open"}}})

    def boom(gl, project, iids):
        raise RuntimeError("api down")
    monkeypatch.setattr(subtasks, "_fetch", boom)
    res = subtasks.poll(ctx)                             # must NOT raise
    assert res["posted"] == 0
    assert ctx.store.get_state("subtasks", f"{PROJECT}#7")["tasks"]["12"]["state"] == "open"


def test_maybe_poll_throttles_and_respects_rule_toggle(ctx, monkeypatch):
    polls = []
    monkeypatch.setattr(subtasks, "poll", lambda c: polls.append(1) or {"issues": 0})
    t0 = dt.datetime(2026, 7, 3, 12, 0, 0)
    assert subtasks.maybe_poll(ctx, now=t0) is not None
    assert subtasks.maybe_poll(ctx, now=t0 + dt.timedelta(minutes=1)) is None   # throttled
    assert subtasks.maybe_poll(ctx, now=t0 + dt.timedelta(minutes=4)) is not None
    assert len(polls) == 2
    # admin override turns the rule off -> no poll, no API traffic
    ctx.settings.update_rule("subtask", {"enabled": False})
    assert subtasks.maybe_poll(ctx, now=t0 + dt.timedelta(minutes=10)) is None
    assert len(polls) == 2


def test_rule_absent_from_config_disables_the_poll(ctx, monkeypatch):
    ctx.config = {"rules": []}
    monkeypatch.setattr(subtasks, "poll", lambda c: pytest.fail("must not poll"))
    subtasks._reset_poll_state()
    assert subtasks.maybe_poll(ctx) is None


def test_template_renders_all_three_actions(ctx):
    from botkit.notify.render import Renderer
    tpl_dir = pathlib.Path(subtasks.__file__).parent / "templates"
    r = Renderer(tpl_dir)
    base = {"iid": "12", "title": "Внутренняя карточка", "url": "https://gl/x/-/work_items/12"}
    for action, marker in (("open", "Создана"), ("close", "Закрыта"), ("reopen", "Переоткрыта")):
        html = r.render("subtask", "matrix", {**base, "action": action})
        assert marker in html and "#12" in html and "Внутренняя карточка" in html


def test_fetch_parses_hierarchy_payload(monkeypatch):
    # wire-format guard for the GraphQL shape (17.6: project.workItems + hierarchy
    # widget); non-Task children are ignored, states normalize to open/closed.
    class FakeGL:
        def graphql(self, query, variables):
            assert variables == {"p": PROJECT, "iids": ["7"]}
            return {"project": {"workItems": {"nodes": [{
                "iid": "7", "state": "OPEN",
                "widgets": [
                    {},
                    {"children": {"nodes": [
                        {"iid": "12", "title": "т", "state": "CLOSED",
                         "workItemType": {"name": "Task"}},
                        {"iid": "13", "title": "не таск", "state": "OPEN",
                         "workItemType": {"name": "Objective"}},
                    ]}},
                ]}]}}}
    out = subtasks._fetch(FakeGL(), PROJECT, ["7"])
    assert out == {"7": {"state": "open",
                         "tasks": {"12": {"title": "т", "state": "closed"}}}}
