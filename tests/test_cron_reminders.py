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


# run_due_soon / run_overdue now return the uniform result dict
# ({"sent", "reason", "recipients", "html"}); these helpers read the count.
def _sent(result) -> int:
    return result["sent"]


# --- due_soon ------------------------------------------------------------
def test_due_soon_fires_once_per_due_tomorrow_issue(tmp_path):
    store = Store(path=str(tmp_path / "s.db"))
    engine = _Engine()
    issues = [_issue(1, gid=11, due=TOMORROW), _issue(2, gid=22, due=TOMORROW)]
    assert _sent(cron.run_due_soon(engine, issues, store)) == 2
    assert len(engine.handled) == 2


def test_due_soon_dedups_second_run_same_day(tmp_path):
    store = Store(path=str(tmp_path / "s.db"))
    engine = _Engine()
    issues = [_issue(1, gid=11, due=TOMORROW)]
    assert _sent(cron.run_due_soon(engine, issues, store)) == 1
    assert _sent(cron.run_due_soon(engine, issues, store)) == 0   # already sent today
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
    assert _sent(cron.run_due_soon(engine, issues, store)) == 0
    assert engine.handled == []


# --- overdue -------------------------------------------------------------
def test_overdue_fires_for_past_due(tmp_path):
    store = Store(path=str(tmp_path / "s.db"))
    engine = _Engine()
    issues = [_issue(1, gid=11, due=YESTERDAY), _issue(2, gid=22, due=YESTERDAY)]
    assert _sent(cron.run_overdue(engine, issues, store)) == 2
    assert len(engine.handled) == 2


def test_overdue_dedups_second_run_same_day(tmp_path):
    store = Store(path=str(tmp_path / "s.db"))
    engine = _Engine()
    issues = [_issue(1, gid=11, due=YESTERDAY)]
    assert _sent(cron.run_overdue(engine, issues, store)) == 1
    assert _sent(cron.run_overdue(engine, issues, store)) == 0
    assert len(engine.handled) == 1


def test_overdue_skips_today_and_future_and_undated(tmp_path):
    store = Store(path=str(tmp_path / "s.db"))
    engine = _Engine()
    issues = [
        _issue(1, gid=11, due=TODAY.isoformat()),   # due today is NOT overdue
        _issue(2, gid=22, due=TOMORROW),
        _issue(3, gid=33, due=None),
    ]
    assert _sent(cron.run_overdue(engine, issues, store)) == 0
    assert engine.handled == []


def test_overdue_not_marked_when_undelivered(tmp_path):
    # An undelivered event must NOT burn the dedup slot, so a retry still fires.
    store = Store(path=str(tmp_path / "s.db"))
    issues = [_issue(1, gid=11, due=YESTERDAY)]
    assert _sent(cron.run_overdue(_Engine(deliver=False), issues, store)) == 0
    assert _sent(cron.run_overdue(_Engine(deliver=True), issues, store)) == 1


# --- skey namespacing ----------------------------------------------------
def test_skey_namespaces_dedup_across_sources(tmp_path):
    # Two sources sharing the same global issue id must each fire independently.
    store = Store(path=str(tmp_path / "s.db"))
    engine = _Engine()
    issues = [_issue(1, gid=11, due=TOMORROW)]
    assert _sent(cron.run_due_soon(engine, issues, store, skey="a")) == 1
    assert _sent(cron.run_due_soon(engine, issues, store, skey="b")) == 1   # different namespace
    assert _sent(cron.run_due_soon(engine, issues, store, skey="a")) == 0   # a already fired


# --- manual (commit=False): stateless + repeatable + recipients ----------
def test_due_soon_manual_repeatable_no_ledger(tmp_path):
    # commit=False ignores the per-issue dedup -> re-sends on every call, and
    # writes NO `sent` rows so it can't perturb the scheduler.
    store = Store(path=str(tmp_path / "s.db"))
    engine = _Engine()
    issues = [_issue(1, gid=11, due=TOMORROW)]
    r1 = cron.run_due_soon(engine, issues, store, commit=False)
    r2 = cron.run_due_soon(engine, issues, store, commit=False)
    assert r1["sent"] == 1 and r2["sent"] == 1          # both send (no dedup)
    assert len(engine.handled) == 2
    assert not store.already_sent("due_soon", "11")     # nothing marked


def test_overdue_manual_recipients_are_assignee_logins(tmp_path):
    # overdue is a dm pass -> recipients = assignee logins it would DM.
    store = Store(path=str(tmp_path / "s.db"))
    engine = _Engine()
    issue = {"id": 11, "iid": 1, "due_date": YESTERDAY, "title": "t", "web_url": "u",
             "assignees": [{"username": "misha"}, {"username": "d.nikulin"}]}
    r = cron.run_overdue(engine, [issue], store, commit=False)
    assert r["reason"] == "ok"
    assert set(r["recipients"]) == {"misha", "d.nikulin"}


def test_overdue_dry_renders_without_dispatch(tmp_path):
    # dry=True: no engine.handle, no ledger, but recipients populated.
    store = Store(path=str(tmp_path / "s.db"))
    engine = _Engine()
    issue = {"id": 11, "iid": 1, "due_date": YESTERDAY, "title": "t", "web_url": "u",
             "assignees": [{"username": "misha"}]}
    r = cron.run_overdue(engine, [issue], store, commit=False, dry=True)
    assert r["sent"] == 0 and r["reason"] == "ok"
    assert r["recipients"] == ["misha"]
    assert engine.handled == []                          # dry never dispatches
    assert not store.already_sent("overdue", "11")


# --- run_one: the dispatch the scheduler + admin both go through ----------
class _GitLab:
    """GitLab client fake: returns a fixed `opened` issue list."""
    def __init__(self, issues):
        self._issues = issues

    def group_issues(self, *a, **kw):
        return self._issues


class _DigestEngine:
    """Engine fake for run_one digest passes (handle + match + renderer)."""
    def __init__(self):
        self.handled: list = []
        self.renderer = self

    def handle(self, event):
        self.handled.append(event)
        return {"sent": ["room"]}

    def match(self, event):
        return {"template": event.kind}

    def render(self, template, channel, ctx):
        return f"<{template}>"


def test_run_one_team_full_manual_is_repeatable_and_stateless(tmp_path):
    # admin path: team_full (forces full mode), commit=False -> sends every click,
    # writes no ledger (and a full pass never touches the delta baseline anyway).
    store = Store(path=str(tmp_path / "s.db"))
    engine = _DigestEngine()
    gl = _GitLab([_issue(1, gid=11, due=YESTERDAY)])
    r1 = cron.run_one(engine, gl, "g1", store, "team_full", commit=False, room="!r", skey="s1")
    r2 = cron.run_one(engine, gl, "g1", store, "team_full", commit=False, room="!r", skey="s1")
    assert r1["sent"] == 1 and r2["sent"] == 1
    assert r1["recipients"] == ["room:!r"]
    assert r1["html"] == "<digest_team>"
    assert not store.already_sent("digest_team", "s1:group")     # no ledger
    assert store.get_state("digest_team", "s1:group") is None     # no baseline


def test_run_one_team_full_commit_marks_dedup_but_not_baseline(tmp_path):
    # scheduler path: team_full on a committed run writes the per-day dedup, sends
    # the full overview, but does NOT write the baseline (that's team_delta's).
    # A second run the same day is a no-op (dedup holds).
    store = Store(path=str(tmp_path / "s.db"))
    engine = _DigestEngine()
    gl = _GitLab([_issue(1, gid=11, due=YESTERDAY)])
    r1 = cron.run_one(engine, gl, "g1", store, "team_full", room="!r", skey="s1")
    assert r1["sent"] == 1
    assert store.already_sent("digest_team", "s1:group")          # mark_sent written
    assert store.get_state("digest_team", "s1:group") is None     # baseline untouched
    r2 = cron.run_one(engine, gl, "g1", store, "team_full", room="!r", skey="s1")
    assert r2["sent"] == 0                                         # deduped
    assert len(engine.handled) == 1


def test_run_one_team_delta_commit_writes_baseline(tmp_path):
    # scheduler path: team_delta is the SOLE baseline owner. First committed run
    # with no baseline learns it silently (no_baseline); the snapshot is written.
    store = Store(path=str(tmp_path / "s.db"))
    engine = _DigestEngine()
    gl = _GitLab([_issue(1, gid=11, due=YESTERDAY)])
    r = cron.run_one(engine, gl, "g1", store, "team_delta", room="!r", skey="s1")
    assert r["sent"] == 0 and r["reason"] == "no_baseline"
    assert store.get_state("digest_team", "s1:group") is not None  # baseline learned
    assert engine.handled == []
