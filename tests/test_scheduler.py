"""scheduler.due_now / pass_due: a pass fires only when enabled, today is in its
days, and now is within the grace window — or, past the grace window, only when
an established schedule (a prior run on an earlier day) missed today's slot."""
from __future__ import annotations

from types import SimpleNamespace

import scheduler
from scheduler import GRACE_MINUTES, _hhmm_to_min, due_now, pass_due

WED = 2
THU = 3


def test_hhmm_to_min():
    assert _hhmm_to_min("00:00") == 0
    assert _hhmm_to_min("09:00") == 540
    assert _hhmm_to_min("09:30") == 570
    assert _hhmm_to_min("23:59") == 1439


def _cfg(**kw):
    base = {"enabled": True, "days": [WED], "time": "09:00"}
    base.update(kw)
    return base


def test_due_now_disabled():
    assert due_now(_cfg(enabled=False), WED, 540) is False


def test_due_now_wrong_weekday():
    assert due_now(_cfg(days=[WED]), THU, 540) is False


def test_due_now_at_scheduled_time():
    assert due_now(_cfg(time="09:00"), WED, 540) is True       # exactly t


def test_due_now_before_time():
    assert due_now(_cfg(time="09:00"), WED, 539) is False      # one min early


def test_due_now_grace_window_inclusive_upper():
    t = 540
    assert due_now(_cfg(time="09:00"), WED, t + GRACE_MINUTES) is True   # t+grace OK


def test_due_now_past_grace_window():
    t = 540
    assert due_now(_cfg(time="09:00"), WED, t + GRACE_MINUTES + 1) is False


def test_due_now_default_time_when_missing():
    cfg = {"enabled": True, "days": [WED]}      # no 'time' -> defaults to 09:00
    assert due_now(cfg, WED, 540) is True


def test_due_now_default_empty_days():
    cfg = {"enabled": True, "time": "09:00"}    # no 'days' -> never matches
    assert due_now(cfg, WED, 540) is False


def test_grace_window_is_two_hours():
    # documents the constant the boundary tests rely on
    assert scheduler.GRACE_MINUTES == 120


# --- pass_due: the three-branch logic (grace window + downtime recovery) --
# Branches 1 & 2 (before-time / within-grace) match due_now exactly, regardless
# of has_prior_run. Branch 3 (past grace) is due IFF an established schedule
# missed today's slot.
def test_pass_due_disabled():
    assert pass_due(_cfg(enabled=False), WED, 540, has_prior_run=True) is False


def test_pass_due_wrong_weekday():
    assert pass_due(_cfg(days=[WED]), THU, 540, has_prior_run=True) is False


def test_pass_due_before_time_never_due():
    assert pass_due(_cfg(time="09:00"), WED, 539, has_prior_run=True) is False
    assert pass_due(_cfg(time="09:00"), WED, 539, has_prior_run=False) is False


def test_pass_due_within_grace_due_regardless_of_history():
    t = 540
    # within the grace window it's due whether or not it ran before (on-time path)
    assert pass_due(_cfg(time="09:00"), WED, t, has_prior_run=False) is True
    assert pass_due(_cfg(time="09:00"), WED, t + GRACE_MINUTES, has_prior_run=False) is True
    assert pass_due(_cfg(time="09:00"), WED, t + GRACE_MINUTES, has_prior_run=True) is True


def test_pass_due_past_grace_recovers_only_with_prior_run():
    t = 540
    late = t + GRACE_MINUTES + 1
    # established schedule that missed the slot -> recover
    assert pass_due(_cfg(time="09:00"), WED, late, has_prior_run=True) is True
    # fresh deploy / first day -> do NOT catch up (anti-storm)
    assert pass_due(_cfg(time="09:00"), WED, late, has_prior_run=False) is False


# --- tick() gates on the active-hours window --------------------------
# A minimal ctx: tick checks the kill switch / scheduler_on / is_nonworking /
# is_active_now BEFORE iterating sources. We make sources.enabled() raise so any
# tick that gets past the active-hours gate is caught — i.e. the gate, not luck,
# is what stops the pass loop.
class _Boom(Exception):
    pass


def _ctx(active_now: bool):
    settings = SimpleNamespace(
        enabled=lambda: True,
        get_global=lambda: {"scheduler_on": True},
        is_nonworking=lambda iso: False,
        is_active_now=lambda now: active_now,
        conn_value=lambda field: "",
    )

    def _enabled():
        raise _Boom("pass loop reached")             # past the active-hours gate

    sources = SimpleNamespace(enabled=_enabled)
    return SimpleNamespace(settings=settings, store=None, sources=sources)


def test_tick_skips_all_passes_outside_active_window():
    # Outside the window -> tick returns before touching sources (no _Boom).
    scheduler.tick(_ctx(active_now=False))           # must NOT raise


def test_tick_runs_pass_loop_inside_active_window():
    # Inside the window -> tick proceeds to the pass loop (our sentinel fires).
    try:
        scheduler.tick(_ctx(active_now=True))
    except _Boom:
        return
    raise AssertionError("tick did not reach the pass loop inside the active window")
