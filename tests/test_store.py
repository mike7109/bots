"""Store: the dedup ledger + JSON state snapshots that keep the bot from
losing a task or double-pinging. All against a tmp DB — never /data."""
from __future__ import annotations

from botkit.store import Store


def _store(tmp_path) -> Store:
    return Store(path=str(tmp_path / "state.db"))


# --- sent ledger (already_sent / mark_sent) ------------------------------
def test_mark_sent_flips_already_sent(tmp_path):
    s = _store(tmp_path)
    assert s.already_sent("overdue", 42, day="2026-06-05") is False
    s.mark_sent("overdue", 42, day="2026-06-05")
    assert s.already_sent("overdue", 42, day="2026-06-05") is True


def test_mark_sent_is_idempotent(tmp_path):
    s = _store(tmp_path)
    s.mark_sent("overdue", 42, day="2026-06-05")
    s.mark_sent("overdue", 42, day="2026-06-05")  # INSERT OR IGNORE: no error, no dup
    assert s.already_sent("overdue", 42, day="2026-06-05") is True


def test_already_sent_keyed_by_kind_key_day(tmp_path):
    s = _store(tmp_path)
    s.mark_sent("overdue", 42, day="2026-06-05")
    # different day -> resets
    assert s.already_sent("overdue", 42, day="2026-06-06") is False
    # different key -> independent
    assert s.already_sent("overdue", 99, day="2026-06-05") is False
    # different kind -> independent
    assert s.already_sent("due_soon", 42, day="2026-06-05") is False


def test_mark_sent_key_int_and_str_are_same(tmp_path):
    s = _store(tmp_path)
    s.mark_sent("overdue", 42, day="2026-06-05")          # int key
    assert s.already_sent("overdue", "42", day="2026-06-05") is True  # str key -> same row


# --- state snapshots (get/set/clear/list) --------------------------------
def test_set_get_state_roundtrip_json(tmp_path):
    s = _store(tmp_path)
    assert s.get_state("digest", "x") is None
    value = {"a": 1, "b": [1, 2, 3], "nested": {"k": "v"}}
    s.set_state("digest", "x", value)
    assert s.get_state("digest", "x") == value


def test_set_state_preserves_unicode(tmp_path):
    s = _store(tmp_path)
    value = {"title": "Михаил Бахмутский ✅", "emoji": "⏰🚧"}
    s.set_state("digest", "u", value)
    assert s.get_state("digest", "u") == value


def test_set_state_on_conflict_updates(tmp_path):
    s = _store(tmp_path)
    s.set_state("digest", "x", {"v": 1})
    s.set_state("digest", "x", {"v": 2})       # ON CONFLICT DO UPDATE
    assert s.get_state("digest", "x") == {"v": 2}


def test_clear_state(tmp_path):
    s = _store(tmp_path)
    s.set_state("digest", "x", {"v": 1})
    s.clear_state("digest", "x")
    assert s.get_state("digest", "x") is None


def test_list_state_returns_all_rows_for_kind(tmp_path):
    s = _store(tmp_path)
    s.set_state("source", "a", {"name": "A"})
    s.set_state("source", "b", {"name": "B"})
    s.set_state("other", "c", {"name": "C"})
    assert s.list_state("source") == {"a": {"name": "A"}, "b": {"name": "B"}}
    assert s.list_state("missing") == {}


def test_state_key_int_str_normalised(tmp_path):
    s = _store(tmp_path)
    s.set_state("k", 7, {"v": 1})
    assert s.get_state("k", "7") == {"v": 1}


# --- last-run history (record_run / last_run) ----------------------------
def test_last_run_none_before_any_record(tmp_path):
    s = _store(tmp_path)
    assert s.last_run("src1:digest") is None


def test_record_run_roundtrip(tmp_path):
    s = _store(tmp_path)
    s.record_run("src1:digest", "2026-06-05", ok=True)
    rec = s.last_run("src1:digest")
    assert rec["iso"] == "2026-06-05"
    assert rec["ok"] is True
    assert "ts" in rec and rec["detail"] == ""


def test_record_run_stores_failure_detail_truncated(tmp_path):
    s = _store(tmp_path)
    s.record_run("src1:team", "2026-06-05", ok=False, detail="x" * 500)
    rec = s.last_run("src1:team")
    assert rec["ok"] is False
    assert len(rec["detail"]) == 300       # detail[:300]


def test_record_run_overwrites_previous(tmp_path):
    s = _store(tmp_path)
    s.record_run("src1:digest", "2026-06-04", ok=True)
    s.record_run("src1:digest", "2026-06-05", ok=True)   # later run wins
    assert s.last_run("src1:digest")["iso"] == "2026-06-05"


# --- activity log / stats ------------------------------------------------
def test_log_event_and_recent_log(tmp_path):
    s = _store(tmp_path)
    s.log_event("issue", "open", "room", "sent", "#1 hello")
    s.log_event("issue", "close", "room", "skipped", "#2 bye")
    rows = s.recent_log()
    assert len(rows) == 2
    assert rows[0]["status"] == "skipped"      # newest first (id DESC)
    assert rows[1]["status"] == "sent"
    only_sent = s.recent_log(status="sent")
    assert len(only_sent) == 1 and only_sent[0]["detail"] == "#1 hello"


def test_log_stats_counts_today(tmp_path):
    s = _store(tmp_path)
    s.log_event("issue", "open", "room", "sent")
    s.log_event("issue", "open", "room", "sent")
    s.log_event("issue", "close", "room", "skipped")
    stats = s.log_stats()
    assert stats["today"]["sent"] == 2
    assert stats["today"]["skipped"] == 1
    assert stats["by_kind"].get("issue") == 2   # only 'sent' counted in by_kind
