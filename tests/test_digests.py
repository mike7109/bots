"""digests: bucketing, column extraction, the delta engine, and the stats
helpers. Mostly pure functions (no engine/store, deterministic dates); the
metrics once-per-week dedup test uses a real tmp Store + tiny fakes."""
from __future__ import annotations

import digests
from botkit.store import Store


# --- _bucket boundaries --------------------------------------------------
def test_bucket_boundaries():
    today, horizon = "2026-06-05", "2026-06-12"
    assert digests._bucket("", today, horizon) == "none"
    assert digests._bucket(None, today, horizon) == "none"
    assert digests._bucket("2026-06-04", today, horizon) == "overdue"
    assert digests._bucket("2026-06-05", today, horizon) == "today"
    assert digests._bucket("2026-06-06", today, horizon) == "soon"     # > today
    assert digests._bucket("2026-06-12", today, horizon) == "soon"     # == horizon
    assert digests._bucket("2026-06-13", today, horizon) == "later"    # past horizon


# --- _column: deterministic (alphabetically-smallest) workflow:: label ---
def test_column_none_when_no_workflow_label():
    assert digests._column([]) is None
    assert digests._column(None) is None
    assert digests._column(["bug", "frontend"]) is None


def test_column_single_workflow_label():
    assert digests._column(["workflow::in progress"]) == "in progress"


def test_column_picks_alphabetically_smallest_of_many():
    # On Free tier workflow:: labels aren't mutually exclusive, so an issue can
    # carry several; REST label ORDER isn't guaranteed, so _column must be
    # deterministic. It returns the alphabetically-smallest matching column
    # ("in progress" < "review"), independent of input order.
    labels = ["bug", "workflow::review", "workflow::in progress"]
    assert digests._column(labels) == "in progress"
    # reversed input -> same result (order-independence is the whole point)
    assert digests._column(list(reversed(labels))) == "in progress"


# --- _diff: the delta engine that prevents daily-repeat spam -------------
def _snap_row(iid, *, due="", col=None, bucket="none", title="t", url="u", assignees=None):
    return {iid: {"iid": iid, "title": title, "url": url, "due": due,
                  "col": col, "assignees": assignees or [], "bucket": bucket}}


def test_diff_new_and_removed():
    prev = _snap_row("1")
    cur = _snap_row("2")
    out = digests._diff(prev, cur)
    assert [r["iid"] for r in out["new"]] == ["2"]
    assert [r["iid"] for r in out["removed"]] == ["1"]


def test_diff_moved_column():
    prev = _snap_row("1", col="todo")
    cur = _snap_row("1", col="in progress")
    out = digests._diff(prev, cur)
    assert len(out["moved"]) == 1
    assert out["moved"][0]["from"] == "todo"
    assert out["moved"][0]["col"] == "in progress"


def test_diff_changed_due_reported_as_due():
    prev = _snap_row("1", due="2026-06-10", bucket="soon")
    cur = _snap_row("1", due="2026-06-05", bucket="today")
    out = digests._diff(prev, cur)
    assert len(out["due"]) == 1
    assert out["due"][0]["from_due"] == "2026-06-10"


def test_diff_changed_due_suppresses_bucket_cross():
    # NOTE: current behavior — the `elif c["bucket"] != p["bucket"]` branch means
    # a changed due date is reported ONLY as 'due'; the bucket crossing it caused
    # (e.g. soon -> overdue) is intentionally NOT also reported as overdue/today.
    prev = _snap_row("1", due="2026-06-10", bucket="soon")
    cur = _snap_row("1", due="2026-06-01", bucket="overdue")
    out = digests._diff(prev, cur)
    assert len(out["due"]) == 1
    assert out["overdue"] == []                    # suppressed by the elif branch
    assert out["today"] == []


def test_diff_bucket_cross_to_overdue_without_due_change():
    # Same due date, but time passed -> crossed into overdue.
    prev = _snap_row("1", due="2026-06-04", bucket="soon")
    cur = _snap_row("1", due="2026-06-04", bucket="overdue")
    out = digests._diff(prev, cur)
    assert out["due"] == []
    assert [r["iid"] for r in out["overdue"]] == ["1"]
    assert out["today"] == []


def test_diff_bucket_cross_to_today():
    prev = _snap_row("1", due="2026-06-05", bucket="soon")
    cur = _snap_row("1", due="2026-06-05", bucket="today")
    out = digests._diff(prev, cur)
    assert [r["iid"] for r in out["today"]] == ["1"]
    assert out["overdue"] == []


def test_diff_no_change_is_empty():
    prev = _snap_row("1", due="2026-06-05", col="todo", bucket="today")
    cur = _snap_row("1", due="2026-06-05", col="todo", bucket="today")
    out = digests._diff(prev, cur)
    assert not any(out.values())


def test_diff_moved_and_due_both_reported():
    # column move AND due change are independent (two separate `if`s).
    prev = _snap_row("1", due="2026-06-10", col="todo", bucket="soon")
    cur = _snap_row("1", due="2026-06-05", col="done", bucket="today")
    out = digests._diff(prev, cur)
    assert len(out["moved"]) == 1
    assert len(out["due"]) == 1


# --- _percentile (nearest-rank) ------------------------------------------
def test_percentile_empty_is_none():
    assert digests._percentile([], 0.85) is None


def test_percentile_p85_of_1_to_10():
    # nearest-rank: rank = ceil(0.85 * 10) = 9 -> ordered[8] = 9
    assert digests._percentile(list(range(1, 11)), 0.85) == 9


def test_percentile_floor_rank_is_at_least_one():
    # tiny q -> rank floored to 1 -> smallest value
    assert digests._percentile([5, 1, 9], 0.0) == 1


def test_percentile_p100():
    assert digests._percentile([3, 1, 2], 1.0) == 3


# --- _median -------------------------------------------------------------
def test_median_empty_is_none():
    assert digests._median([]) is None


def test_median_odd():
    assert digests._median([3, 1, 2]) == 2          # sorted [1,2,3] -> middle


def test_median_even():
    assert digests._median([1, 2, 3, 4]) == 2.5     # avg of 2,3


# --- metrics: weekly dedup is robust to weekday ---------------------------
class _CountingEngine:
    """Engine stub: every handled event "delivers" and is counted."""
    def __init__(self):
        self.handled = 0

    def handle(self, event):
        self.handled += 1
        return {"sent": ["room"]}


class _EmptyGitLab:
    """GitLab client stub: no issues at all (metrics still emits its snapshot)."""
    def group_issues(self, *a, **kw):
        return []


def test_metrics_once_per_week_regardless_of_weekday(tmp_path, monkeypatch):
    # Two metrics() runs on DIFFERENT weekdays of the SAME ISO week must send at
    # most once: the dedup pins `day` to the week_key, so the (kind,key,day) PK
    # collapses the whole week into one slot.
    store = Store(path=str(tmp_path / "state.db"))
    engine, gl = _CountingEngine(), _EmptyGitLab()

    import datetime as dt

    class _MonDate(dt.date):                 # both days fall in ISO week 2026-W23
        @classmethod
        def today(cls):
            return cls(2026, 6, 1)           # Monday

    class _WedDate(dt.date):
        @classmethod
        def today(cls):
            return cls(2026, 6, 3)           # Wednesday, same ISO week

    monkeypatch.setattr(digests.dt, "date", _MonDate)
    assert digests.metrics(engine, gl, "g1", store, skey="s1") == 1   # first send

    monkeypatch.setattr(digests.dt, "date", _WedDate)
    assert digests.metrics(engine, gl, "g1", store, skey="s1") == 0   # same week -> no-op
    assert engine.handled == 1               # engine.handle called exactly once


# --- parse_day / parse_days ----------------------------------------------
def test_parse_days():
    assert digests.parse_days("wed,fri") == frozenset({2, 4})
    assert digests.parse_days("wed, junk, mon") == frozenset({2, 0})


def test_parse_day_fallback():
    assert digests.parse_day("mon") == 0
    assert digests.parse_day("garbage", default=3) == 3
