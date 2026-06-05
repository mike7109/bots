"""SendGuard (rate-limit + circuit breaker) and its Engine.handle integration.

The guard is a SAFETY mechanism: a runaway pass must trip it, which STOPS all
further sending and fires `on_trip` once. Tests inject a fake clock and use
in-memory dict-backed tripped state so each cap (global / per-target /
duplicate), auto-reset, manual reset, disabled mode, idempotent on_trip, and
exception-safety are exercised in isolation — plus the Engine wiring (blocked()
sends nothing; a guard that trips after N sends stops the next handle()).
"""
from __future__ import annotations

import pytest

from botkit.notify.engine import Engine
from botkit.notify.event import Event
from botkit.notify.guard import SendGuard

from test_engine import FakeRenderer, FakeTransport  # reuse the engine fakes


class FakeClock:
    """A clock you can advance by hand: clock() returns the current value."""
    def __init__(self, t=1000.0):
        self.t = float(t)

    def __call__(self):
        return self.t

    def advance(self, dt):
        self.t += dt
        return self.t


class State:
    """In-memory persistent-state stand-in for get_tripped/set_tripped."""
    def __init__(self):
        self.value = {"tripped": False}
        self.trips = []          # reasons passed to on_trip

    def get(self):
        return dict(self.value)

    def set(self, v):
        self.value = dict(v)

    def on_trip(self, reason):
        self.trips.append(reason)


def make_guard(state, clock, **over):
    cfg = dict(enabled=True, window_s=600, max_global=50, target_window_s=300,
               max_per_target=5, max_duplicate=3, auto_reset_s=0)
    cfg.update(over)
    return SendGuard(get_tripped=state.get, set_tripped=state.set,
                     on_trip=state.on_trip, clock=clock, **cfg)


# --- caps ----------------------------------------------------------------
def test_trips_on_global_cap():
    st, clk = State(), FakeClock()
    # tiny caps so we don't loop forever; raise per-target/dup so global wins
    g = make_guard(st, clk, max_global=3, max_per_target=99, max_duplicate=99)
    for i in range(3):                       # 3 == cap, not yet over
        g.record(f"t{i}", f"msg{i}")
    assert g.blocked() is False
    g.record("t4", "msg4")                   # 4th send -> over the global cap
    assert g.blocked() is True
    assert st.value["tripped"] is True
    assert "глобальный" in st.value["reason"]
    assert len(st.trips) == 1


def test_trips_on_per_target_cap():
    st, clk = State(), FakeClock()
    g = make_guard(st, clk, max_per_target=2, max_global=99, max_duplicate=99)
    # different content each time (so duplicate cap never fires), same target
    g.record("alice", "a")
    g.record("alice", "b")
    assert g.blocked() is False               # 2 == cap
    g.record("alice", "c")                    # 3rd to alice -> over per-target
    assert g.blocked() is True
    assert "alice" in st.value["reason"]
    # a different target would not have tripped at the same volume
    assert len(st.trips) == 1


def test_trips_on_duplicate_cap():
    st, clk = State(), FakeClock()
    g = make_guard(st, clk, max_duplicate=2, max_global=99, max_per_target=99)
    # same content, but distinct targets so per-target never fires
    g.record("t1", "identical")
    g.record("t2", "identical")
    assert g.blocked() is False               # 2 == cap
    g.record("t3", "identical")               # 3rd identical -> over duplicate
    assert g.blocked() is True
    assert "дубликат" in st.value["reason"].lower()


def test_window_pruning_avoids_false_trip():
    # Sends spread out beyond the window must NOT accumulate into a trip.
    st, clk = State(), FakeClock()
    g = make_guard(st, clk, window_s=100, max_global=2, target_window_s=100,
                   max_per_target=99, max_duplicate=99)
    g.record("t", "x1")
    clk.advance(60)
    g.record("t", "x2")
    clk.advance(60)                           # first is now >100s old -> pruned
    g.record("t", "x3")                       # window holds only 2 -> no trip
    assert g.blocked() is False
    assert st.value["tripped"] is False


# --- blocked() / disabled / reset / auto-reset ---------------------------
def test_disabled_guard_never_trips_or_blocks():
    st, clk = State(), FakeClock()
    g = make_guard(st, clk, enabled=False, max_global=1, max_per_target=1,
                   max_duplicate=1)
    for i in range(20):
        g.record("t", "same")                 # would massively exceed every cap
    assert g.blocked() is False
    assert st.value["tripped"] is False
    assert st.trips == []


def test_blocked_true_after_trip_false_after_reset():
    st, clk = State(), FakeClock()
    g = make_guard(st, clk, max_global=1, max_per_target=99, max_duplicate=99)
    g.record("a", "1")
    g.record("b", "2")                        # over global cap of 1
    assert g.blocked() is True
    g.reset()
    assert g.blocked() is False
    assert st.value == {"tripped": False}


def test_reset_clears_in_memory_window():
    st, clk = State(), FakeClock()
    g = make_guard(st, clk, max_global=2, max_per_target=99, max_duplicate=99)
    g.record("a", "1")
    g.record("b", "2")
    g.record("c", "3")                        # trips
    assert g.blocked() is True
    g.reset()                                 # clears tripped + window
    # after reset the next two sends should not re-trip immediately
    g.record("d", "4")
    g.record("e", "5")
    assert g.blocked() is False


def test_auto_reset_after_cooldown():
    st, clk = State(), FakeClock()
    g = make_guard(st, clk, max_global=1, max_per_target=99, max_duplicate=99,
                   auto_reset_s=300)
    g.record("a", "1")
    g.record("b", "2")                        # trips at t=1000
    assert g.blocked() is True
    clk.advance(299)
    assert g.blocked() is True                # cooldown not elapsed yet
    clk.advance(2)                            # now 301s since trip >= 300
    assert g.blocked() is False               # auto-reset
    assert st.value == {"tripped": False}


def test_no_auto_reset_when_disabled_cooldown():
    st, clk = State(), FakeClock()
    g = make_guard(st, clk, max_global=1, max_per_target=99, max_duplicate=99,
                   auto_reset_s=0)
    g.record("a", "1")
    g.record("b", "2")
    assert g.blocked() is True
    clk.advance(10_000)                       # huge gap, but auto_reset_s=0
    assert g.blocked() is True                # stays tripped until manual reset


# --- on_trip semantics ---------------------------------------------------
def test_on_trip_called_exactly_once_per_trip():
    st, clk = State(), FakeClock()
    g = make_guard(st, clk, max_global=1, max_per_target=99, max_duplicate=99)
    g.record("a", "1")
    g.record("b", "2")                        # trips
    g.record("c", "3")                        # already tripped -> no new on_trip
    g.record("d", "4")
    assert len(st.trips) == 1
    # after a reset a fresh trip fires on_trip again
    g.reset()
    g.record("e", "5")
    g.record("f", "6")
    assert len(st.trips) == 2


def test_trip_is_idempotent():
    st, clk = State(), FakeClock()
    g = make_guard(st, clk)
    g.trip("first")
    since = st.value["since"]
    g.trip("second")                          # no-op while tripped
    assert st.value["reason"] == "first"
    assert st.value["since"] == since
    assert st.trips == ["first"]


# --- exception safety ----------------------------------------------------
def test_record_never_raises_when_set_tripped_raises():
    st, clk = State(), FakeClock()

    def boom_set(_v):
        raise RuntimeError("db down")

    g = SendGuard(enabled=True, window_s=600, max_global=1, target_window_s=300,
                  max_per_target=99, max_duplicate=99, auto_reset_s=0,
                  get_tripped=st.get, set_tripped=boom_set, on_trip=st.on_trip,
                  clock=clk)
    g.record("a", "1")
    g.record("b", "2")                        # would trip -> set_tripped raises
    # must not propagate; on_trip still attempted (best-effort) even though the
    # persist failed — the operator should still hear about it.
    assert len(st.trips) == 1
    assert "глобальный лимит" in st.trips[0]


def test_trip_never_raises_when_on_trip_raises():
    st, clk = State(), FakeClock()

    def boom_trip(_r):
        raise RuntimeError("alert pipe down")

    g = SendGuard(enabled=True, window_s=600, max_global=1, target_window_s=300,
                  max_per_target=99, max_duplicate=99, auto_reset_s=0,
                  get_tripped=st.get, set_tripped=st.set, on_trip=boom_trip,
                  clock=clk)
    g.record("a", "1")
    g.record("b", "2")                        # trips; on_trip raises internally
    assert st.value["tripped"] is True        # state still persisted


def test_blocked_fails_open_when_get_tripped_raises():
    clk = FakeClock()

    def boom_get():
        raise RuntimeError("db down")

    g = SendGuard(enabled=True, window_s=600, max_global=50, target_window_s=300,
                  max_per_target=5, max_duplicate=3, auto_reset_s=0,
                  get_tripped=boom_get, set_tripped=lambda v: None,
                  on_trip=lambda r: None, clock=clk)
    assert g.blocked() is False               # fail OPEN, don't wedge the bot


# --- Engine.handle integration ------------------------------------------
def _engine(transports, settings, guard, defaults=None, rules=None):
    rules = rules or [{"event": "issue", "template": "t", "to": ["room"]}]
    config = {"rules": rules, "defaults": defaults or {"to": ["room"]}}
    return Engine(config, FakeRenderer(), transports, settings=settings, guard=guard)


def _issue(action="open"):
    return Event(kind="issue", action=action, iid="1", title="hello")


@pytest.fixture
def store(tmp_path):
    from botkit.store import Store
    return Store(path=str(tmp_path / "state.db"))


@pytest.fixture
def settings(store):
    from settings import Settings
    return Settings(store)


def test_engine_blocked_sends_nothing_and_records_ignored(settings, store):
    st, clk = State(), FakeClock()
    st.value = {"tripped": True, "since": clk.t, "reason": "boom"}
    g = make_guard(st, clk)
    t = FakeTransport()
    eng = _engine({"room": t}, settings, g)
    result = eng.handle(_issue())
    assert result["ignored"] == "breaker tripped"
    assert t.dispatched == []                          # nothing dispatched
    rows = store.recent_log(status="ignored")
    assert rows and "предохранитель" in rows[0]["detail"]


def test_engine_guard_trips_after_n_and_stops_next(settings):
    st, clk = State(), FakeClock()
    # global cap 2: the 3rd actual send trips, the 4th handle is blocked
    g = make_guard(st, clk, max_global=2, max_per_target=99, max_duplicate=99)
    room = FakeTransport(outcome={"room": "!r:srv"})
    eng = _engine({"room": room}, settings, g)
    assert eng.handle(_issue())["sent"] == ["room"]    # 1
    assert eng.handle(_issue())["sent"] == ["room"]    # 2
    assert eng.handle(_issue())["sent"] == ["room"]    # 3 -> records, trips after
    assert g.blocked() is True
    r4 = eng.handle(_issue())                          # 4 -> blocked
    assert r4["ignored"] == "breaker tripped"
    assert len(room.dispatched) == 3                   # only 3 ever delivered


def test_engine_records_dm_target_keys(settings):
    # A DM outcome listing two mxids must record under "dm:<mxid>" each, so a
    # loop hammering ONE person trips the per-target cap.
    st, clk = State(), FakeClock()
    g = make_guard(st, clk, max_per_target=2, max_global=99, max_duplicate=99)
    # transport returns a delivery to the same single mxid each handle
    dm = FakeTransport(outcome={"dm_to": ["@bob:srv"]})
    eng = _engine({"dm": dm}, settings, g,
                  rules=[{"event": "issue", "template": "t", "to": ["dm"]}],
                  defaults={"to": ["dm"]})
    eng.handle(_issue())                               # bob: 1
    eng.handle(_issue())                               # bob: 2
    assert g.blocked() is False
    eng.handle(_issue())                               # bob: 3 -> over per-target
    assert g.blocked() is True
    assert "dm:@bob:srv" in st.value["reason"]


def test_engine_skipped_send_not_recorded(settings):
    # A declined ({"skipped"}) outcome must NOT count toward the guard.
    st, clk = State(), FakeClock()
    g = make_guard(st, clk, max_global=1, max_per_target=99, max_duplicate=99)
    t = FakeTransport(outcome={"skipped": "no assignee"})
    eng = _engine({"dm": t}, settings, g,
                  rules=[{"event": "issue", "template": "t", "to": ["dm"]}],
                  defaults={"to": ["dm"]})
    for _ in range(5):
        eng.handle(_issue())                           # all skipped
    assert g.blocked() is False                        # nothing recorded -> no trip


def test_engine_without_guard_unaffected(settings):
    # guard=None (default) must behave exactly as before.
    t = FakeTransport()
    eng = Engine({"rules": [{"event": "issue", "template": "t", "to": ["room"]}],
                  "defaults": {"to": ["room"]}},
                 FakeRenderer(), {"room": t}, settings=settings)
    assert eng.guard is None
    assert eng.handle(_issue())["sent"] == ["room"]
