"""Settings: DB override beats baseline default, for every layer."""
from __future__ import annotations

import datetime as dt

import pytest
from markupsafe import Markup

from botkit.store import Store
from settings import ACTIVE_HOURS_DEFAULTS, PASS_DEFAULTS, Settings


@pytest.fixture
def store(tmp_path):
    return Store(path=str(tmp_path / "state.db"))


@pytest.fixture
def settings(store):
    return Settings(store)


# --- global -----------------------------------------------------------
def test_global_default_then_override(settings):
    assert settings.enabled() is True               # baseline default
    settings.update_global({"enabled": False})
    assert settings.enabled() is False              # DB override wins


def test_update_global_merges(settings):
    settings.update_global({"enabled": False})
    settings.update_global({"scheduler_on": False})
    g = settings.get_global()
    assert g["enabled"] is False and g["scheduler_on"] is False


# --- per-user prefs ---------------------------------------------------
def test_user_pref_default(settings):
    assert settings.user_pref("bob") == {"muted": False, "push": "default"}


def test_update_user_override(settings):
    settings.update_user("bob", {"muted": True})
    assert settings.is_muted("bob") is True
    assert settings.is_muted("alice") is False      # independent per user


def test_push_notice_modes(settings):
    # default mode -> defer to the message's default
    assert settings.push_notice("bob", True) is True
    assert settings.push_notice("bob", False) is False
    settings.update_user("bob", {"push": "quiet"})
    assert settings.push_notice("bob", False) is True   # quiet forces notice
    settings.update_user("bob", {"push": "loud"})
    assert settings.push_notice("bob", True) is False   # loud forces ping


# --- per-pass schedule ------------------------------------------------
def test_pass_schedule_default(settings):
    # A calendar pass gets the registry defaults plus a normalised kind="daily"
    # (Phase 3a tolerates old/missing kind so the scheduler can branch on it).
    assert settings.pass_schedule("digest_full") == {**PASS_DEFAULTS["digest_full"], "kind": "daily"}


def test_pass_schedule_override_merges_over_default(settings):
    settings.update_pass("digest_full", {"time": "07:30"})
    sched = settings.pass_schedule("digest_full")
    assert sched["time"] == "07:30"                 # overridden
    assert sched["days"] == PASS_DEFAULTS["digest_full"]["days"]   # kept from default
    assert sched["enabled"] == PASS_DEFAULTS["digest_full"]["enabled"]


def test_pass_schedule_unknown_name_fallback(settings):
    assert settings.pass_schedule("nope") == {
        "enabled": True, "days": [0, 1, 2, 3, 4], "time": "09:00", "kind": "daily"}


# --- Phase 3a: the delta digests carry an INTERVAL schedule ------------
def test_pass_schedule_delta_passes_are_interval(settings):
    # Both delta digests merge the interval fields (kind + cadence/threshold/floor)
    # over their registry defaults; the field values match INTERVAL_DELTA_DEFAULTS.
    for name in ("digest_delta", "team_delta"):
        sched = settings.pass_schedule(name)
        assert sched["kind"] == "interval"
        assert sched["every_hours"] == 2.5
        assert sched["floor_min"] == 20
        assert sched["change_threshold"] == 5
        assert sched["days"] == [0, 1, 2, 3, 4]


def test_pass_schedule_calendar_passes_default_to_daily(settings):
    # Every non-delta scheduled pass is kind="daily" (so the scheduler routes it
    # down the calendar path).
    for name in ("due", "overdue", "digest_full", "team_full", "triage", "stale", "metrics"):
        assert settings.pass_schedule(name)["kind"] == "daily"


def test_pass_schedule_tolerates_old_override_without_kind(settings):
    # A pre-Phase-3a override row (no `kind`) on an interval pass must NOT demote
    # it to daily: the registry default kind="interval" survives the merge.
    settings.update_pass("digest_delta", {"days": [0, 1, 3], "time": "08:00"})
    sched = settings.pass_schedule("digest_delta")
    assert sched["kind"] == "interval"               # kept from registry default
    assert sched["days"] == [0, 1, 3] and sched["time"] == "08:00"   # override applied


def test_delta_quiet_after_full_default(settings):
    assert settings.get_global()["delta_quiet_after_full_h"] == 3
    settings.update_global({"delta_quiet_after_full_h": 0})
    assert settings.get_global()["delta_quiet_after_full_h"] == 0


# --- rule overrides ---------------------------------------------------
def test_rule_override_none_then_set(settings):
    assert settings.rule_override("issue") is None
    settings.update_rule("issue", {"enabled": False})
    assert settings.rule_override("issue") == {"enabled": False}
    settings.update_rule("issue", {"to": ["dm"]})   # merges
    assert settings.rule_override("issue") == {"enabled": False, "to": ["dm"]}


# --- operator alerts --------------------------------------------------
def test_alerts_default(settings):
    assert settings.get_alerts() == {"engineers": [], "enabled": True}


def test_alerts_update_and_roundtrip(settings, store):
    eff = settings.update_alerts({"engineers": ["misha", "bob"], "enabled": False})
    assert eff == {"engineers": ["misha", "bob"], "enabled": False}
    # round-trip via a fresh Settings over the same store (DB-backed)
    assert Settings(store).get_alerts() == {"engineers": ["misha", "bob"], "enabled": False}


def test_alerts_update_merges(settings):
    settings.update_alerts({"engineers": ["misha"]})
    settings.update_alerts({"enabled": False})       # patch keeps engineers
    assert settings.get_alerts() == {"engineers": ["misha"], "enabled": False}


def test_alerts_validates_types(settings):
    # non-list engineers ignored; non-bool enabled coerced
    settings.update_alerts({"engineers": ["a"], "enabled": 1})
    settings.update_alerts({"engineers": "notalist"})   # ignored -> kept
    a = settings.get_alerts()
    assert a["engineers"] == ["a"] and a["enabled"] is True


# --- active-hours window ----------------------------------------------
def _at(h, m=0):
    return dt.datetime(2026, 6, 5, h, m)


def test_active_hours_defaults(settings):
    assert settings.get_active_hours() == {
        "enabled": True, "from": "08:00", "until": "20:00", "webhooks_quiet": False}
    # the module default matches the getter
    assert settings.get_active_hours() == ACTIVE_HOURS_DEFAULTS


def test_active_hours_update_and_roundtrip(settings, store):
    eff = settings.update_active_hours({"from": "09:00", "webhooks_quiet": True})
    assert eff["from"] == "09:00" and eff["webhooks_quiet"] is True
    assert eff["until"] == "20:00"                   # untouched -> default
    # round-trip via a fresh Settings over the same store (DB-backed)
    fresh = Settings(store).get_active_hours()
    assert fresh["from"] == "09:00" and fresh["webhooks_quiet"] is True


def test_active_hours_update_merges(settings):
    settings.update_active_hours({"from": "07:30"})
    settings.update_active_hours({"enabled": False})   # patch keeps `from`
    a = settings.get_active_hours()
    assert a["from"] == "07:30" and a["enabled"] is False


def test_active_hours_validates_hhmm(settings):
    # bad HH:MM ignored (kept at default); bool coerced
    settings.update_active_hours({"from": "9:00", "until": "25:99", "enabled": 1})
    a = settings.get_active_hours()
    assert a["from"] == "08:00" and a["until"] == "20:00"   # garbage rejected
    assert a["enabled"] is True
    # a valid value still lands
    settings.update_active_hours({"from": "06:15"})
    assert settings.get_active_hours()["from"] == "06:15"


def test_is_active_now_inside_window(settings):
    # default 08:00–20:00
    assert settings.is_active_now(_at(8, 0)) is True       # lower bound inclusive
    assert settings.is_active_now(_at(12, 30)) is True
    assert settings.is_active_now(_at(20, 0)) is True      # upper bound inclusive


def test_is_active_now_outside_window(settings):
    assert settings.is_active_now(_at(7, 59)) is False
    assert settings.is_active_now(_at(20, 1)) is False
    assert settings.is_active_now(_at(3, 0)) is False


def test_is_active_now_disabled_always_active(settings):
    settings.update_active_hours({"enabled": False})
    assert settings.is_active_now(_at(3, 0)) is True       # disabled -> always on
    assert settings.is_active_now(_at(23, 0)) is True


def test_is_active_now_reversed_window_is_active(settings):
    # from > until is unsupported in v1: don't brick -> treat as always-active
    settings.update_active_hours({"from": "20:00", "until": "08:00"})
    assert settings.is_active_now(_at(3, 0)) is True
    assert settings.is_active_now(_at(12, 0)) is True


def test_is_active_now_bad_config_fails_open(settings, store):
    # malformed HH:MM written directly to the DB (bypassing validation) -> active
    store.set_state("settings", "active_hours", {"enabled": True, "from": "oops", "until": "20:00"})
    assert settings.is_active_now(_at(3, 0)) is True


# --- connection settings + seed_conn ----------------------------------
def test_get_conn_defaults_empty(settings):
    assert settings.get_conn() == {
        "matrix_homeserver": "", "matrix_token": "", "webhook_secret": "",
        "gitlab_url": "", "poke_token": ""}


def test_poke_token_is_a_conn_field(settings):
    # masked/editable like webhook_secret via the conn machinery
    settings.update_conn({"poke_token": "tok-123"})
    assert settings.conn_value("poke_token") == "tok-123"
    settings.update_conn({"poke_token": ""})         # cleared -> falls back to env
    assert settings.conn_value("poke_token") == ""


def test_poke_token_seeded_from_env(store, monkeypatch):
    # seed pulls POKE_TOKEN from env once (env var -> CONN_FIELDS["poke_token"])
    monkeypatch.setenv("POKE_TOKEN", "from-env")
    s = Settings(store)
    s.seed_conn()
    assert s.conn_value("poke_token") == "from-env"


# --- poke anti-spam config --------------------------------------------
def test_poke_defaults(settings):
    assert settings.get_poke() == {
        "cooldown_s": 600, "per_user_window_s": 600, "per_user_max": 4}


def test_poke_cap_stays_below_breaker_per_target(settings):
    # A legit poke must never be the message that trips the global breaker.
    assert settings.get_poke()["per_user_max"] < settings.get_guard()["max_per_target"]


def test_poke_update_and_roundtrip(settings, store):
    eff = settings.update_poke({"cooldown_s": 120, "per_user_max": 2})
    assert eff["cooldown_s"] == 120 and eff["per_user_max"] == 2
    assert eff["per_user_window_s"] == 600           # untouched -> default
    assert Settings(store).get_poke()["cooldown_s"] == 120


def test_poke_update_validates_ints(settings):
    settings.update_poke({"cooldown_s": "oops", "per_user_max": -3})
    p = settings.get_poke()
    assert p["cooldown_s"] == 600                     # garbage ignored
    assert p["per_user_max"] == 0                     # negative clamped to 0


def test_update_conn_set_and_clear(settings):
    settings.update_conn({"gitlab_url": "https://gl.example"})
    assert settings.conn_value("gitlab_url") == "https://gl.example"
    settings.update_conn({"gitlab_url": ""})        # cleared -> removed
    assert settings.conn_value("gitlab_url") == ""


def test_seed_conn_one_time(settings, store, monkeypatch):
    # seed pulls from env only once; afterwards DB is authoritative.
    monkeypatch.setenv("GITLAB_URL", "https://seeded.example")
    settings.seed_conn()
    assert settings.conn_value("gitlab_url") == "https://seeded.example"
    # a later env change must NOT re-seed (conn row already exists)
    monkeypatch.setenv("GITLAB_URL", "https://changed.example")
    settings.seed_conn()
    assert settings.conn_value("gitlab_url") == "https://seeded.example"


def test_seed_conn_skips_when_already_present(settings, store, monkeypatch):
    settings.update_conn({"gitlab_url": "https://manual.example"})
    monkeypatch.setenv("GITLAB_URL", "https://env.example")
    settings.seed_conn()                            # conn already present -> no-op
    assert settings.conn_value("gitlab_url") == "https://manual.example"


# --- send guard config ------------------------------------------------
def test_guard_defaults(settings):
    g = settings.get_guard()
    assert g == {
        "enabled": True, "window_s": 600, "max_global": 50,
        "target_window_s": 300, "max_per_target": 5,
        "max_duplicate": 3, "auto_reset_s": 0,
    }


def test_guard_defaults_clear_a_legit_digest_burst(settings):
    # A full personal digest DMs every assignee plus the team room in one minute;
    # the defaults must sit ABOVE that so normal operation never trips.
    g = settings.get_guard()
    legit_burst = 11 + 1                              # ~11 assignees + team message
    assert g["max_global"] > legit_burst
    assert g["max_per_target"] >= 5


def test_guard_update_and_roundtrip(settings, store):
    eff = settings.update_guard({"enabled": False, "max_global": 100})
    assert eff["enabled"] is False and eff["max_global"] == 100
    assert eff["max_per_target"] == 5                # untouched -> default
    # round-trip via a fresh Settings over the same store
    assert Settings(store).get_guard()["max_global"] == 100


def test_guard_update_validates_types(settings):
    # ints coerced, bools coerced, garbage ignored (keeps prior/default)
    settings.update_guard({"max_global": "75", "enabled": 1, "window_s": "oops"})
    g = settings.get_guard()
    assert g["max_global"] == 75 and g["enabled"] is True
    assert g["window_s"] == 600                      # garbage ignored


def test_guard_update_clamps_negative_to_zero(settings):
    settings.update_guard({"max_duplicate": -5})
    assert settings.get_guard()["max_duplicate"] == 0


# --- breaker trip state (persistent) ----------------------------------
def test_breaker_default(settings):
    assert settings.get_breaker() == {"tripped": False, "since": None, "reason": ""}


def test_breaker_set_and_roundtrip(settings, store):
    settings.set_breaker({"tripped": True, "since": 1234.5, "reason": "flood"})
    b = settings.get_breaker()
    assert b["tripped"] is True and b["since"] == 1234.5 and b["reason"] == "flood"
    # survives a fresh Settings over the same store (persistent across restart)
    assert Settings(store).get_breaker()["tripped"] is True


def test_breaker_reset_clears_fields(settings):
    settings.set_breaker({"tripped": True, "since": 1.0, "reason": "x"})
    settings.set_breaker({"tripped": False})
    assert settings.get_breaker() == {"tripped": False, "since": None, "reason": ""}


# --- label display whitelist (🏷 which chips appear) -------------------
def test_label_display_default_shows_all(settings):
    cfg = settings.get_label_display()
    assert cfg == {"enabled": False, "allow": []}
    # disabled -> labels unchanged
    assert settings.display_labels(["bug", "security"]) == ["bug", "security"]


def test_label_display_empty_allow_shows_all(settings):
    settings.update_label_display({"enabled": True, "allow": []})
    assert settings.display_labels(["bug", "x"]) == ["bug", "x"]   # empty list = show all


def test_label_display_filters_when_enabled(settings):
    settings.update_label_display({"enabled": True, "allow": ["security", "bug"]})
    assert settings.display_labels(["bug", "workflow::wip", "security"]) == ["bug", "security"]


def test_label_display_normalizes_allow(settings):
    settings.update_label_display({"allow": [" bug ", "bug", "", "security"]})
    assert settings.get_label_display()["allow"] == ["bug", "security"]   # trimmed + deduped


def test_label_display_merge_patch(settings):
    settings.update_label_display({"allow": ["a"]})
    settings.update_label_display({"enabled": True})        # doesn't wipe allow
    cfg = settings.get_label_display()
    assert cfg["enabled"] is True and cfg["allow"] == ["a"]


# --- watched issues (special tags -> rooms) ---------------------------
def test_watched_default_empty(settings):
    assert settings.get_watched() == []
    assert settings.watched_targets(["security"]) == []


def test_watched_set_normalizes_and_assigns_ids(settings):
    out = settings.set_watched([
        {"name": "Sec", "tags": [" security ", "security", ""], "rooms": ["!a:s", "!a:s", "!b:s"]},
        {"name": "", "tags": ["x"], "rooms": ["!c:s"], "enabled": False},
    ])
    assert out[0]["id"] == "w1" and out[0]["tags"] == ["security"] and out[0]["rooms"] == ["!a:s", "!b:s"]
    assert out[1]["id"] == "w2" and out[1]["enabled"] is False


def test_watched_targets_matches_any_tag(settings):
    settings.set_watched([{"name": "Sec", "tags": ["security", "incident"], "rooms": ["!sec:s"]}])
    assert [r["rooms"] for r in settings.watched_targets(["bug", "incident"])] == [["!sec:s"]]
    assert settings.watched_targets(["bug"]) == []         # no tag overlap


def test_watched_targets_skips_disabled_and_roomless(settings):
    settings.set_watched([
        {"name": "off", "tags": ["a"], "rooms": ["!r:s"], "enabled": False},
        {"name": "noroom", "tags": ["a"], "rooms": []},
    ])
    assert settings.watched_targets(["a"]) == []


def test_watched_persists_across_restart(settings, store):
    settings.set_watched([{"name": "Sec", "tags": ["security"], "rooms": ["!sec:s"]}])
    assert Settings(store).watched_targets(["security"])[0]["name"] == "Sec"


# --- note (comment) truncation config ---------------------------------
def test_note_defaults(settings):
    assert settings.get_note() == {"enabled": True, "max_chars": 500, "max_lines": 8}


def test_note_update_and_roundtrip(settings, store):
    eff = settings.update_note({"enabled": False, "max_chars": 120})
    assert eff["enabled"] is False and eff["max_chars"] == 120
    assert eff["max_lines"] == 8                              # untouched -> default
    assert Settings(store).get_note()["max_chars"] == 120


def test_note_clamps_to_ceiling(settings):
    settings.update_note({"max_chars": 99999, "max_lines": 999})
    n = settings.get_note()
    assert n["max_chars"] == 4000 and n["max_lines"] == 50    # hard ceiling
    settings.update_note({"max_chars": 0, "max_lines": -3})
    n = settings.get_note()
    assert n["max_chars"] == 1 and n["max_lines"] == 1        # floor at 1 (never empty)


def test_note_update_ignores_garbage(settings):
    settings.update_note({"max_chars": "oops"})
    assert settings.get_note()["max_chars"] == 500            # kept at default


def test_clip_comment_short_is_untouched_and_markup(settings):
    out = settings.clip_comment("строка1\nстрока2", "https://gl/7#note_1")
    assert isinstance(out, Markup)
    assert str(out) == "строка1<br>строка2"                   # \n -> <br>, no marker


def test_clip_comment_escapes_html(settings):
    out = str(settings.clip_comment("<script>alert(1)</script>", ""))
    assert "<script>" not in out and "&lt;script&gt;" in out  # escaped once


def test_clip_comment_truncates_by_chars(settings):
    settings.update_note({"max_chars": 10})
    out = str(settings.clip_comment("a" * 50, "https://gl/7#note_1"))
    assert "✂️ (обрезано" in out and "href=" in out
    assert out.startswith("a" * 10) and not out.startswith("a" * 11)   # exactly 10 body chars


def test_clip_comment_truncates_by_lines(settings):
    settings.update_note({"max_lines": 3})
    out = str(settings.clip_comment("\n".join(f"l{i}" for i in range(10)), ""))
    assert "✂️ (обрезано" in out
    assert "l0<br>l1<br>l2" in out and "l3" not in out


def test_clip_comment_image_markdown_to_emoji(settings):
    out = str(settings.clip_comment("см ![скрин](http://x/y.png) тут", ""))
    assert "\U0001f5bc" in out and "![" not in out and "](" not in out


def test_clip_comment_ceiling_applies_when_disabled(settings):
    settings.update_note({"enabled": False})
    assert str(settings.clip_comment("hi", "")) == "hi"       # short -> full, no marker
    out = str(settings.clip_comment("z" * 5000, ""))          # over the 4000 hard ceiling
    assert "✂️ (обрезано" in out and out.count("z") == 4000


def test_clip_comment_blocks_dangerous_url(settings):
    settings.update_note({"max_chars": 3})
    out = str(settings.clip_comment("hello", "javascript:alert(1)"))
    assert 'href="#"' in out                                  # _safe_url neutralised the scheme


# --- note per-room throttle (breaker-safe) ----------------------------
def test_note_send_allowed_sheds_above_cap(settings):
    # cap = max_per_target - 1 = 4; the 5th send to the same room is shed.
    room = "!sec:s"
    assert [settings.note_send_allowed(room) for _ in range(5)] == [True, True, True, True, False]


def test_note_send_allowed_cap_below_breaker(settings):
    # A legit comment can never be the send that trips the per-target breaker.
    cap = max(1, settings.get_guard()["max_per_target"] - 1)
    assert cap < settings.get_guard()["max_per_target"]


def test_note_send_allowed_rooms_independent(settings):
    for _ in range(4):
        settings.note_send_allowed("!a:s")
    assert settings.note_send_allowed("!a:s") is False        # !a:s exhausted
    assert settings.note_send_allowed("!b:s") is True         # independent budget


def test_note_send_allowed_inert_when_guard_off(settings):
    settings.update_guard({"enabled": False})
    assert all(settings.note_send_allowed("!x:s") for _ in range(20))   # nothing to protect
