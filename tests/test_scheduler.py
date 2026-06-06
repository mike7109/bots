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


# === Phase 3a: interval_should_send — the pure send-decision helper ==========
# Modelled on Alertmanager: send iff there are changes AND not quieted-after-full
# AND (cadence ceiling elapsed OR a change-threshold burst).
from scheduler import interval_should_send   # noqa: E402


def _send(**kw):
    base = dict(has_changes=True, n_changes=1, elapsed_since_send_h=10.0,
                every_hours=2.5, change_threshold=5, in_quiet_after_full=False)
    base.update(kw)
    return interval_should_send(**base)


def test_interval_send_when_cadence_elapsed():
    # changes present + elapsed >= every_hours -> send (regular cadence).
    assert _send(elapsed_since_send_h=3.0, n_changes=1) is True


def test_interval_send_early_on_burst_even_within_cadence():
    # within the cadence window but >= threshold changes -> early send.
    assert _send(elapsed_since_send_h=0.5, n_changes=5) is True
    assert _send(elapsed_since_send_h=0.5, n_changes=6) is True


def test_interval_hold_when_below_threshold_and_within_cadence():
    # changes exist but few, and not enough time passed -> HOLD (no send).
    assert _send(elapsed_since_send_h=0.5, n_changes=4) is False


def test_interval_never_sends_without_changes():
    # no changes -> never send, regardless of elapsed/threshold.
    assert _send(has_changes=False, elapsed_since_send_h=100.0, n_changes=99) is False


def test_interval_never_sends_during_quiet_after_full():
    # a recent *_full quieted the delta: even a big burst is suppressed.
    assert _send(in_quiet_after_full=True, n_changes=99, elapsed_since_send_h=100.0) is False


def test_interval_first_send_when_never_sent_before():
    # elapsed is None (never sent) -> cadence considered elapsed -> send on change.
    assert _send(elapsed_since_send_h=None, n_changes=1) is True


# === Phase 3a: the interval path in tick() (eval-throttle + cadence + ledger) =
import datetime as dt   # noqa: E402

import cron             # noqa: E402
import keys             # noqa: E402
from botkit.store import Store   # noqa: E402

MON = dt.date(2026, 6, 1)        # a workday in cfg.days for the delta passes


class _Engine:
    """Engine fake: handle delivers + records; match/render power dry previews."""
    def __init__(self):
        self.handled: list = []
        self.renderer = self
        self.settings = self

    def handle(self, event):
        self.handled.append(event)
        return {"sent": ["room"]}

    def match(self, event):
        return {"template": event.kind}

    def render(self, template, channel, ctx):
        return f"<{template}>"

    def pass_schedule(self, name):
        return {}


class _GitLab:
    def __init__(self, issues):
        self._issues = list(issues)

    def set(self, issues):
        self._issues = list(issues)

    def group_issues(self, *a, **kw):
        return list(self._issues)


def _issue(iid, login, *, due=None):
    return {"id": iid, "iid": iid, "due_date": due, "title": f"i{iid}",
            "web_url": f"u/{iid}", "labels": [], "assignees": [{"username": login}],
            "updated_at": "2000-01-01T00:00:00Z", "created_at": "2000-01-01T00:00:00Z"}


def _interval_ctx(store, engine, gl, *, only=("digest_delta",), now=None,
                  quiet_h=0, group=None):
    """A ctx wired for the interval path: a single source, real Store, fakes for
    engine/gl. `only` restricts cron.PASSES to the named interval pass(es) so the
    test exercises just them (the global gates are all open)."""
    alerts = SimpleNamespace(alert_pass_failure=lambda *a, **k: None)
    glob = {"scheduler_on": True, "delta_quiet_after_full_h": quiet_h}

    def _pass_schedule(name):
        # The interval delta schedule (kind=interval) with small floor for tests.
        return {"enabled": True, "kind": "interval", "days": [0, 1, 2, 3, 4],
                "every_hours": 2.5, "floor_min": 20, "change_threshold": 5,
                "time": "09:00"}

    settings = SimpleNamespace(
        enabled=lambda: True,
        get_global=lambda: glob,
        is_nonworking=lambda iso: False,
        is_active_now=lambda n: True,
        conn_value=lambda field: "",
        pass_schedule=_pass_schedule,
    )
    sources = SimpleNamespace(enabled=lambda: [{"id": "s1", "group_id": "g1", "room": "!r"}])
    return SimpleNamespace(settings=settings, store=store, sources=sources,
                           engine=engine, alerter=alerts), only


def _freeze_now(monkeypatch, now):
    """Freeze scheduler's wall clock at `now`."""
    class _DT(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return now
    monkeypatch.setattr(scheduler.dt, "datetime", _DT)


def _patch(monkeypatch, *, now, only, gl):
    """Freeze the scheduler AND digests clocks at `now`, inject the fake GitLab
    client (tick builds its own `GitLabClient` per source), and restrict
    cron.PASSES to `only`. Freezing digests' `date.today()` too keeps the delta
    baseline and the live run_one snapshot on the SAME day, so only the issue
    edits the test makes register as changes (no spurious date-drift diffs)."""
    import digests
    _freeze_now(monkeypatch, now)

    class _Date(dt.date):
        @classmethod
        def today(cls):
            return now.date()
    monkeypatch.setattr(digests.dt, "date", _Date)
    monkeypatch.setattr(scheduler, "GitLabClient", lambda *a, **k: gl)
    monkeypatch.setattr(cron, "PASSES", tuple(only))
    scheduler._reset_interval_state()


def test_interval_pass_does_not_touch_sched_day_ledger(tmp_path, monkeypatch):
    # The interval delta must NEVER use the per-day SCHED ledger: its cadence is
    # governed by the last-SEND timestamp + baseline, so a same-day re-eval can
    # send again. After a real send the SCHED row must be ABSENT.
    store = Store(path=str(tmp_path / "s.db"))
    engine, gl = _Engine(), _GitLab([_issue(1, "misha", due="2026-06-10")])
    ctx, only = _interval_ctx(store, engine, gl)
    t0 = dt.datetime(2026, 6, 1, 10, 0)
    _patch(monkeypatch, now=t0, only=only, gl=gl)
    # Seed a baseline so the first real change is a delta (committed delta learns it).
    digests = __import__("digests")
    digests.personal(engine, gl.group_issues(), store, today=MON, full=False, commit=True, skey="s1")
    engine.handled.clear()
    # Now change enough issues to cross the threshold -> a real send.
    gl.set([_issue(i, "misha", due="2026-06-1%d" % i) for i in range(1, 7)])
    scheduler.tick(ctx)
    assert len(engine.handled) == 1                       # one real delta send
    schedkey = keys.ns("s1", "digest_delta")
    assert not store.already_sent(keys.SCHED, schedkey, day="2026-06-01")   # NO day-ledger
    assert store.last_run(schedkey) is not None           # last-SEND recorded (cadence anchor)


def test_interval_eval_throttle_blocks_reeval_within_floor(tmp_path, monkeypatch):
    # Two ticks 5 min apart (< floor_min=20): the SECOND must not even evaluate
    # (no GitLab fetch / no send). A tick past the floor evaluates again.
    store = Store(path=str(tmp_path / "s.db"))
    engine, gl = _Engine(), _GitLab([_issue(1, "misha", due="2026-06-10")])
    ctx, only = _interval_ctx(store, engine, gl)
    digests = __import__("digests")
    digests.personal(engine, gl.group_issues(), store, today=MON, full=False, commit=True, skey="s1")
    engine.handled.clear()
    # Below-threshold change so it would HOLD (we only care that it EVALUATES).
    gl.set([_issue(1, "misha", due="2026-06-03")])

    fetches = {"n": 0}
    orig = gl.group_issues
    def _counting(*a, **kw):
        fetches["n"] += 1
        return orig(*a, **kw)
    gl.group_issues = _counting

    _patch(monkeypatch, now=dt.datetime(2026, 6, 1, 10, 0), only=only, gl=gl)
    scheduler.tick(ctx)
    first = fetches["n"]
    assert first >= 1                                     # evaluated (dry probe fetched)

    # +5 min: within floor -> skipped entirely (no new fetch).
    monkeypatch.setattr(scheduler.dt, "datetime",
                        type("D", (dt.datetime,), {"now": classmethod(lambda c, tz=None: dt.datetime(2026, 6, 1, 10, 5))}))
    scheduler.tick(ctx)
    assert fetches["n"] == first                          # throttled: no re-eval

    # +25 min: past floor -> evaluates again.
    monkeypatch.setattr(scheduler.dt, "datetime",
                        type("D", (dt.datetime,), {"now": classmethod(lambda c, tz=None: dt.datetime(2026, 6, 1, 10, 25))}))
    scheduler.tick(ctx)
    assert fetches["n"] > first                           # re-evaluated after floor


def test_interval_quiet_after_full_suppresses_send(tmp_path, monkeypatch):
    # With a recent last_full marker (within quiet_h) the delta holds even on a
    # big burst; without it (or stale) it sends.
    store = Store(path=str(tmp_path / "s.db"))
    engine, gl = _Engine(), _GitLab([_issue(1, "misha", due="2026-06-10")])
    ctx, only = _interval_ctx(store, engine, gl, quiet_h=3)
    digests = __import__("digests")
    digests.personal(engine, gl.group_issues(), store, today=MON, full=False, commit=True, skey="s1")
    engine.handled.clear()
    gl.set([_issue(i, "misha", due="2026-06-1%d" % i) for i in range(1, 7)])   # big burst

    now = dt.datetime(2026, 6, 1, 12, 0)
    # Fresh full marker (1h ago) -> within the 3h quiet window -> suppressed.
    store.set_state("last_full", keys.ns("s1", keys.DIGEST_PERSONAL),
                    {"ts": (now - dt.timedelta(hours=1)).isoformat(timespec="seconds")})
    _patch(monkeypatch, now=now, only=only, gl=gl)
    scheduler.tick(ctx)
    assert engine.handled == []                           # quieted by the recent full

    # Stale full marker (5h ago) -> outside the window -> sends.
    scheduler._reset_interval_state()
    store.set_state("last_full", keys.ns("s1", keys.DIGEST_PERSONAL),
                    {"ts": (now - dt.timedelta(hours=5)).isoformat(timespec="seconds")})
    scheduler.tick(ctx)
    assert len(engine.handled) == 1                       # old full no longer quiets


def test_full_send_stamps_last_full_marker(tmp_path, monkeypatch):
    # The calendar path: when a *_full pass SENDS, it stamps the last_full marker
    # (shared event_kind key) the interval delta reads for the overlap guard.
    store = Store(path=str(tmp_path / "s.db"))
    engine, gl = _Engine(), _GitLab([_issue(1, "misha", due="2026-06-10")])
    alerts = SimpleNamespace(alert_pass_failure=lambda *a, **k: None)

    def _pass_schedule(name):
        # digest_full is a daily/calendar pass due now (Mon in days, 09:00).
        return {"enabled": True, "kind": "daily", "days": [0, 1, 2, 3, 4], "time": "09:00"}

    settings = SimpleNamespace(
        enabled=lambda: True, get_global=lambda: {"scheduler_on": True},
        is_nonworking=lambda iso: False, is_active_now=lambda n: True,
        conn_value=lambda f: "", pass_schedule=_pass_schedule)
    sources = SimpleNamespace(enabled=lambda: [{"id": "s1", "group_id": "g1", "room": "!r"}])
    ctx = SimpleNamespace(settings=settings, store=store, sources=sources,
                          engine=engine, alerter=alerts)
    _patch(monkeypatch, now=dt.datetime(2026, 6, 1, 9, 30), only=("digest_full",), gl=gl)
    scheduler.tick(ctx)
    assert len(engine.handled) == 1                       # full overview sent
    marker = store.get_state("last_full", keys.ns("s1", keys.DIGEST_PERSONAL))
    assert marker is not None and "ts" in marker          # marker stamped for the delta
