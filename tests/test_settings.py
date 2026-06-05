"""Settings: DB override beats baseline default, for every layer."""
from __future__ import annotations

import pytest

from botkit.store import Store
from settings import PASS_DEFAULTS, Settings


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
    assert settings.pass_schedule("digest") == PASS_DEFAULTS["digest"]


def test_pass_schedule_override_merges_over_default(settings):
    settings.update_pass("digest", {"time": "07:30"})
    sched = settings.pass_schedule("digest")
    assert sched["time"] == "07:30"                 # overridden
    assert sched["days"] == PASS_DEFAULTS["digest"]["days"]   # kept from default
    assert sched["anchor_days"] == PASS_DEFAULTS["digest"]["anchor_days"]


def test_pass_schedule_unknown_name_fallback(settings):
    assert settings.pass_schedule("nope") == {
        "enabled": True, "days": [0, 1, 2, 3, 4], "time": "09:00"}


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


# --- connection settings + seed_conn ----------------------------------
def test_get_conn_defaults_empty(settings):
    assert settings.get_conn() == {
        "matrix_homeserver": "", "matrix_token": "", "webhook_secret": "", "gitlab_url": ""}


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
