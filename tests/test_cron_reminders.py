"""cron._remind / run_due_soon / run_overdue: per-issue reminders.

These two passes share `_remind` (filter -> per-day dedup -> handle -> mark).
The tests pin behaviour the refactor must preserve: due_soon fires once per
due-tomorrow issue and is a no-op on a second run the same day; overdue fires
for past-due issues and likewise dedups; non-matching issues are skipped; and
the source `skey` namespaces the dedup ledger so two sources don't collide.
"""
from __future__ import annotations

import datetime as dt

import cron
from botkit.store import Store

TODAY = dt.date.today()
TOMORROW = (TODAY + dt.timedelta(days=1)).isoformat()
YESTERDAY = (TODAY - dt.timedelta(days=1)).isoformat()
NEXT_WEEK = (TODAY + dt.timedelta(days=7)).isoformat()


class _Engine:
    """Engine stub: delivers every event to ['room'] and counts handled events.

    Set deliver=False to simulate an undelivered event (engine returns {}).
    """
    def __init__(self, deliver: bool = True):
        self.deliver = deliver
        self.handled: list = []

    def handle(self, event):
        self.handled.append(event)
        return {"sent": ["room"]} if self.deliver else {}


def _issue(iid, *, gid=None, due=None):
    return {"id": gid if gid is not None else iid, "iid": iid, "due_date": due,
            "title": f"issue {iid}", "web_url": f"u/{iid}"}


# --- due_soon ------------------------------------------------------------
def test_due_soon_fires_once_per_due_tomorrow_issue(tmp_path):
    store = Store(path=str(tmp_path / "s.db"))
    engine = _Engine()
    issues = [_issue(1, gid=11, due=TOMORROW), _issue(2, gid=22, due=TOMORROW)]
    assert cron.run_due_soon(engine, issues, store) == 2
    assert len(engine.handled) == 2


def test_due_soon_dedups_second_run_same_day(tmp_path):
    store = Store(path=str(tmp_path / "s.db"))
    engine = _Engine()
    issues = [_issue(1, gid=11, due=TOMORROW)]
    assert cron.run_due_soon(engine, issues, store) == 1
    assert cron.run_due_soon(engine, issues, store) == 0   # already sent today
    assert len(engine.handled) == 1


def test_due_soon_skips_non_matching(tmp_path):
    store = Store(path=str(tmp_path / "s.db"))
    engine = _Engine()
    issues = [
        _issue(1, gid=11, due=YESTERDAY),     # overdue, not "tomorrow"
        _issue(2, gid=22, due=TODAY.isoformat()),
        _issue(3, gid=33, due=NEXT_WEEK),
        _issue(4, gid=44, due=None),          # no due date
    ]
    assert cron.run_due_soon(engine, issues, store) == 0
    assert engine.handled == []


# --- overdue -------------------------------------------------------------
def test_overdue_fires_for_past_due(tmp_path):
    store = Store(path=str(tmp_path / "s.db"))
    engine = _Engine()
    issues = [_issue(1, gid=11, due=YESTERDAY), _issue(2, gid=22, due=YESTERDAY)]
    assert cron.run_overdue(engine, issues, store) == 2
    assert len(engine.handled) == 2


def test_overdue_dedups_second_run_same_day(tmp_path):
    store = Store(path=str(tmp_path / "s.db"))
    engine = _Engine()
    issues = [_issue(1, gid=11, due=YESTERDAY)]
    assert cron.run_overdue(engine, issues, store) == 1
    assert cron.run_overdue(engine, issues, store) == 0
    assert len(engine.handled) == 1


def test_overdue_skips_today_and_future_and_undated(tmp_path):
    store = Store(path=str(tmp_path / "s.db"))
    engine = _Engine()
    issues = [
        _issue(1, gid=11, due=TODAY.isoformat()),   # due today is NOT overdue
        _issue(2, gid=22, due=TOMORROW),
        _issue(3, gid=33, due=None),
    ]
    assert cron.run_overdue(engine, issues, store) == 0
    assert engine.handled == []


def test_overdue_not_marked_when_undelivered(tmp_path):
    # An undelivered event must NOT burn the dedup slot, so a retry still fires.
    store = Store(path=str(tmp_path / "s.db"))
    issues = [_issue(1, gid=11, due=YESTERDAY)]
    assert cron.run_overdue(_Engine(deliver=False), issues, store) == 0
    assert cron.run_overdue(_Engine(deliver=True), issues, store) == 1


# --- skey namespacing ----------------------------------------------------
def test_skey_namespaces_dedup_across_sources(tmp_path):
    # Two sources sharing the same global issue id must each fire independently.
    store = Store(path=str(tmp_path / "s.db"))
    engine = _Engine()
    issues = [_issue(1, gid=11, due=TOMORROW)]
    assert cron.run_due_soon(engine, issues, store, skey="a") == 1
    assert cron.run_due_soon(engine, issues, store, skey="b") == 1   # different namespace
    assert cron.run_due_soon(engine, issues, store, skey="a") == 0   # a already fired
