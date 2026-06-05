"""Alerter: DM engineers about the bot's OWN failures, throttled & best-effort.

Uses a FAKE Matrix (records get_or_create_dm + send_html, can be made to raise)
with a real Identity, Settings and Store(tmp_path). Does NOT import app.py.
"""
from __future__ import annotations

import pytest

from botkit.identity import Identity
from botkit.store import Store

from alerts import Alerter
from settings import Settings

DAY = "2026-06-05"
USERS = {
    "misha": {"matrix": "@misha:srv", "name": "Михаил"},
    "bob":   {"matrix": "@bob:srv",   "name": "Боб"},
    "nomx":  {"name": "Без Матрикса"},      # no matrix id -> unresolvable
}


class FakeMatrix:
    def __init__(self, *, fail_dm: bool = False, fail_send: bool = False):
        self.fail_dm = fail_dm
        self.fail_send = fail_send
        self.dm_calls: list[str] = []
        self.sends: list[dict] = []

    def get_or_create_dm(self, user_id):
        self.dm_calls.append(user_id)
        if self.fail_dm:
            raise RuntimeError("homeserver down")
        return f"!dm-{user_id}"

    def send_html(self, room, html, *, mention_user_ids=None, notice=True, **kw):
        if self.fail_send:
            raise RuntimeError("send failed")
        self.sends.append({"room": room, "html": html,
                           "mentions": list(mention_user_ids or []), "notice": notice})
        return "$evt"


@pytest.fixture
def store(tmp_path):
    return Store(path=str(tmp_path / "state.db"))


@pytest.fixture
def settings(store):
    return Settings(store)


@pytest.fixture
def identity():
    return Identity(USERS)


def _alerter(matrix, identity, settings, store, engineers):
    settings.update_alerts({"engineers": engineers, "enabled": True})
    return Alerter(matrix, identity, settings, store)


def test_alert_dms_each_resolvable_engineer(settings, store, identity):
    mx = FakeMatrix()
    a = _alerter(mx, identity, settings, store, ["misha", "bob"])
    assert a.alert("subj", "det", key="k", day=DAY) is True
    assert mx.dm_calls == ["@misha:srv", "@bob:srv"]
    assert len(mx.sends) == 2
    s = mx.sends[0]
    assert s["room"] == "!dm-@misha:srv"
    assert s["mentions"] == ["@misha:srv"] and s["notice"] is False
    assert "gitlab-notify" in s["html"] and "subj" in s["html"]
    assert store.already_sent("alert", "k", day=DAY) is True


def test_alert_escapes_html(settings, store, identity):
    mx = FakeMatrix()
    a = _alerter(mx, identity, settings, store, ["misha"])
    a.alert("<b>x</b>", "a < b & c", key="k", day=DAY)
    html = mx.sends[0]["html"]
    assert "&lt;b&gt;x&lt;/b&gt;" in html        # subject escaped
    assert "a &lt; b &amp; c" in html            # detail escaped


def test_alert_disabled_noop(settings, store, identity):
    mx = FakeMatrix()
    a = _alerter(mx, identity, settings, store, ["misha"])
    settings.update_alerts({"enabled": False})
    assert a.alert("s", "d", key="k", day=DAY) is False
    assert mx.sends == [] and store.already_sent("alert", "k", day=DAY) is False


def test_alert_dedups_same_key_day(settings, store, identity):
    mx = FakeMatrix()
    a = _alerter(mx, identity, settings, store, ["misha"])
    assert a.alert("s", "d", key="k", day=DAY) is True
    mx.sends.clear()
    assert a.alert("s", "d", key="k", day=DAY) is False   # throttled
    assert mx.sends == []
    # a different day fires again
    assert a.alert("s", "d", key="k", day="2026-06-06") is True


def test_alert_no_engineers_noop(settings, store, identity):
    mx = FakeMatrix()
    a = _alerter(mx, identity, settings, store, [])
    assert a.alert("s", "d", key="k", day=DAY) is False
    assert mx.sends == []


def test_alert_drops_unresolvable_logins(settings, store, identity):
    mx = FakeMatrix()
    a = _alerter(mx, identity, settings, store, ["nomx", "misha"])
    assert a.alert("s", "d", key="k", day=DAY) is True
    assert mx.dm_calls == ["@misha:srv"]          # nomx dropped


def test_send_failure_no_crash_and_no_throttle(settings, store, identity):
    mx = FakeMatrix(fail_send=True)
    a = _alerter(mx, identity, settings, store, ["misha"])
    assert a.alert("s", "d", key="k", day=DAY) is False
    # throttle NOT burned -> a later (working) attempt can still alert
    assert store.already_sent("alert", "k", day=DAY) is False
    mx2 = FakeMatrix()
    a2 = Alerter(mx2, identity, settings, store)
    assert a2.alert("s", "d", key="k", day=DAY) is True


def test_one_bad_recipient_doesnt_stop_rest(settings, store, identity):
    # first DM raises on get_or_create_dm for everyone if fail_dm; here we want a
    # partial: simulate by a matrix that fails send for the FIRST send only.
    class PartialMatrix(FakeMatrix):
        def __init__(self):
            super().__init__()
            self.n = 0

        def send_html(self, room, html, **kw):
            self.n += 1
            if self.n == 1:
                raise RuntimeError("boom")
            return super().send_html(room, html, **kw)

    mx = PartialMatrix()
    a = _alerter(mx, identity, settings, store, ["misha", "bob"])
    assert a.alert("s", "d", key="k", day=DAY) is True   # second delivered
    assert len(mx.sends) == 1
    assert store.already_sent("alert", "k", day=DAY) is True


def test_total_dm_failure_no_throttle(settings, store, identity):
    mx = FakeMatrix(fail_dm=True)
    a = _alerter(mx, identity, settings, store, ["misha", "bob"])
    assert a.alert("s", "d", key="k", day=DAY) is False
    assert store.already_sent("alert", "k", day=DAY) is False


def test_alert_pass_failure_builds_key_and_message(settings, store, identity):
    mx = FakeMatrix()
    a = _alerter(mx, identity, settings, store, ["misha"])
    exc = ValueError("token expired")
    assert a.alert_pass_failure("g12", "digest", exc, day=DAY) is True
    assert store.already_sent("alert", "g12:digest", day=DAY) is True
    html = mx.sends[0]["html"]
    assert "digest" in html and "g12" in html
    assert "ValueError: token expired" in html
