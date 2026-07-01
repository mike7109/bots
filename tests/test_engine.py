"""Engine.match + Engine.handle: rule precedence, the global kill switch,
rule_override 'to'/'enabled' precedence, and skipped-vs-sent accounting.

Built with a tiny fake renderer (returns a string), fake transports (record
calls / return dicts) and a real Settings over a tmp Store — no network, no
templates, no /data."""
from __future__ import annotations

import pytest

from botkit.notify.engine import Engine
from botkit.notify.event import Event
from botkit.store import Store
from settings import Settings


class FakeRenderer:
    """Renders to a marker string; records every call."""
    def __init__(self):
        self.calls = []

    def render(self, template, medium, ctx):
        self.calls.append((template, medium))
        return f"<{template}:{medium}>"


class FakeTransport:
    """Records dispatches; returns whatever `outcome` is configured to be."""
    def __init__(self, medium="matrix", outcome=None):
        self.medium = medium
        self.outcome = outcome if outcome is not None else {"event_id": "$1"}
        self.dispatched = []

    def dispatch(self, event, rule, rendered, defaults):
        self.dispatched.append((event, rule, rendered))
        return self.outcome


class BoomTransport:
    medium = "matrix"

    def dispatch(self, *a, **kw):
        raise RuntimeError("kaboom")


@pytest.fixture
def store(tmp_path):
    return Store(path=str(tmp_path / "state.db"))


@pytest.fixture
def settings(store):
    return Settings(store)


def make_engine(rules, transports, settings, defaults=None):
    config = {"rules": rules, "defaults": defaults or {"to": ["room"]}}
    return Engine(config, FakeRenderer(), transports, settings=settings)


def issue_event(action="open"):
    return Event(kind="issue", action=action, iid="1", title="hello")


# --- match ---------------------------------------------------------------
def test_match_first_rule_wins(settings):
    rules = [
        {"event": "issue", "template": "a"},
        {"event": "issue", "template": "b"},
    ]
    eng = make_engine(rules, {}, settings)
    assert eng.match(issue_event())["template"] == "a"


def test_match_requires_event_kind(settings):
    eng = make_engine([{"event": "task", "template": "t"}], {}, settings)
    assert eng.match(issue_event()) is None


def test_match_actions_filter(settings):
    rules = [{"event": "issue", "actions": ["open", "reopen"], "template": "t"}]
    eng = make_engine(rules, {}, settings)
    assert eng.match(issue_event("open")) is not None
    assert eng.match(issue_event("close")) is None


def test_match_no_actions_matches_any_action(settings):
    rules = [{"event": "issue", "template": "t"}]   # no `actions:` -> any action
    eng = make_engine(rules, {}, settings)
    assert eng.match(issue_event("close")) is not None


# --- handle: kill switch -------------------------------------------------
def test_handle_kill_switch(settings):
    settings.update_global({"enabled": False})
    t = FakeTransport()
    eng = make_engine([{"event": "issue", "template": "t", "to": ["room"]}],
                      {"room": t}, settings)
    result = eng.handle(issue_event())
    assert "ignored" in result
    assert t.dispatched == []                       # nothing dispatched


def test_handle_no_matching_rule(settings):
    eng = make_engine([], {}, settings)
    result = eng.handle(issue_event())
    assert result["ignored"] == "no matching rule"


# --- handle: rule enabled flag + override --------------------------------
def test_handle_rule_disabled_in_config(settings):
    t = FakeTransport()
    eng = make_engine([{"event": "issue", "template": "t", "to": ["room"], "enabled": False}],
                      {"room": t}, settings)
    result = eng.handle(issue_event())
    assert result["ignored"] == "rule disabled"
    assert t.dispatched == []


def test_handle_override_can_enable_a_config_disabled_rule(settings):
    settings.update_rule("issue", {"enabled": True})
    t = FakeTransport()
    eng = make_engine([{"event": "issue", "template": "t", "to": ["room"], "enabled": False}],
                      {"room": t}, settings)
    result = eng.handle(issue_event())
    assert result["sent"] == ["room"]               # override flips it on
    assert len(t.dispatched) == 1


def test_handle_override_can_disable_an_enabled_rule(settings):
    settings.update_rule("issue", {"enabled": False})
    t = FakeTransport()
    eng = make_engine([{"event": "issue", "template": "t", "to": ["room"]}],
                      {"room": t}, settings)
    assert eng.handle(issue_event())["ignored"] == "rule disabled"
    assert t.dispatched == []


def test_handle_override_to_redirects_destination(settings):
    settings.update_rule("issue", {"to": ["dm"]})
    room, dm = FakeTransport(), FakeTransport()
    eng = make_engine([{"event": "issue", "template": "t", "to": ["room"]}],
                      {"room": room, "dm": dm}, settings)
    result = eng.handle(issue_event())
    assert result["sent"] == ["dm"]                 # override 'to' wins
    assert room.dispatched == [] and len(dm.dispatched) == 1


# --- handle: destinations + skipped/sent accounting ----------------------
def test_handle_skipped_outcome_not_counted_as_sent(settings):
    # A transport that declines ({"skipped": ...}) must NOT count as sent.
    t = FakeTransport(outcome={"skipped": "no assignee"})
    eng = make_engine([{"event": "issue", "template": "t", "to": ["dm"]}],
                      {"dm": t}, settings)
    result = eng.handle(issue_event())
    assert result["sent"] == []
    assert result["skipped"] == ["dm"]
    assert len(t.dispatched) == 1                   # it was called, just declined


def test_handle_unknown_destination_is_error(settings, store):
    # No transport for a destination is a MISCONFIG (typo'd `to:` / unimplemented
    # transport), so it must surface as an ERROR, not a silent retryable skip —
    # otherwise callers retry forever and it never reaches anyone.
    eng = make_engine([{"event": "issue", "template": "t", "to": ["email"]}],
                      {}, settings)                 # no 'email' transport registered
    result = eng.handle(issue_event())
    assert result["sent"] == []
    assert result["errors"] == ["email"]
    assert "skipped" not in result
    rows = store.recent_log(status="error")
    assert len(rows) == 1 and rows[0]["channel"] == "email"


def test_handle_dispatch_error_isolated_per_destination(settings):
    good = FakeTransport()
    eng = make_engine([{"event": "issue", "template": "t", "to": ["bad", "room"]}],
                      {"bad": BoomTransport(), "room": good}, settings)
    result = eng.handle(issue_event())
    assert result["sent"] == ["room"]               # good one still delivered
    assert result["errors"] == ["bad"]
    assert len(good.dispatched) == 1


def test_handle_falls_back_to_defaults_to(settings):
    room = FakeTransport()
    eng = make_engine([{"event": "issue", "template": "t"}],   # no 'to' on the rule
                      {"room": room}, settings, defaults={"to": ["room"]})
    result = eng.handle(issue_event())
    assert result["sent"] == ["room"]               # used defaults['to']


def test_handle_records_activity_log_on_sent(settings, store):
    room = FakeTransport()
    eng = make_engine([{"event": "issue", "template": "t", "to": ["room"]}],
                      {"room": room}, settings)
    eng.handle(issue_event())
    rows = store.recent_log(status="sent")
    assert len(rows) == 1 and rows[0]["channel"] == "room"


class RecordingGuard:
    """Guard fake: never blocked, records every _guard_record target key."""
    def __init__(self):
        self.records = []

    def blocked(self):
        return False

    def record(self, key, rendered):
        self.records.append(key)


def test_handle_records_sent_with_guard_by_default(settings):
    g = RecordingGuard()
    t = FakeTransport(outcome={"room": "!r", "event_id": "$1"})
    config = {"rules": [{"event": "issue", "template": "t", "to": ["room"]}], "defaults": {}}
    Engine(config, FakeRenderer(), {"room": t}, settings=settings, guard=g).handle(issue_event())
    assert g.records == ["!r"]                       # normal rule counts toward the breaker


def test_handle_skip_guard_rule_is_not_recorded(settings):
    # A `skip_guard` rule (thread-anchor header) delivers but never pushes the
    # per-target count toward a breaker trip.
    g = RecordingGuard()
    t = FakeTransport(outcome={"room": "!r", "event_id": "$1"})
    config = {"rules": [{"event": "issue", "template": "t", "to": ["room"], "skip_guard": True}],
              "defaults": {}}
    result = Engine(config, FakeRenderer(), {"room": t}, settings=settings, guard=g).handle(issue_event())
    assert result["sent"] == ["room"]                # still delivered
    assert g.records == []                           # but not counted


def test_handle_picks_template_variant_by_medium(settings):
    room = FakeTransport(medium="matrix")
    renderer = FakeRenderer()
    config = {"rules": [{"event": "issue", "template": "issue", "to": ["room"]}],
              "defaults": {}}
    eng = Engine(config, renderer, {"room": room}, settings=settings)
    eng.handle(issue_event())
    assert renderer.calls == [("issue", "matrix")]
