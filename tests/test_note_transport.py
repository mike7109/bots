"""MatrixTransport mention modes.

`mentioned` pings ONLY configured @-logins (Identity.known_matrix_id — no
@login:domain fallback), so a typo'd/group @mention can't resolve to a ghost mxid
and flip a comment notification into a loud highlight. `assignee` keeps the
fallback (assignees are real GitLab users).
"""
from __future__ import annotations

from botkit.identity import Identity
from botkit.notify.event import Event
from botkit.notify.transports.matrix import MatrixTransport

IDENTITY = Identity({"m.bahmutskij": {"matrix": "@m.bahmutskij:fakspro.ru"}},
                    matrix_domain="fakspro.ru")


class FakeClient:
    def __init__(self):
        self.sent = []

    def send_html(self, room, html, mention_user_ids=None, notice=True, thread_root=None):
        self.sent.append({"room": room, "html": html,
                          "mentions": list(mention_user_ids or []), "notice": notice,
                          "thread_root": thread_root})
        return "$evt1"


def _dispatch(event, rule):
    client = FakeClient()
    MatrixTransport(client, identity=IDENTITY).dispatch(event, rule, "<b>hi</b>", {})
    return client.sent[0]


def _dispatch_full(event, rule):
    client = FakeClient()
    outcome = MatrixTransport(client, identity=IDENTITY).dispatch(event, rule, "<b>hi</b>", {})
    return client.sent[0], outcome


def test_mentioned_pings_only_known_users():
    ev = Event(kind="note", action="create", room="!r",
               mention_logins=["m.bahmutskij", "ghost", "grp"])
    out = _dispatch(ev, {"mention": "mentioned", "room_id": "!r"})
    assert out["mentions"] == ["@m.bahmutskij:fakspro.ru"]     # ghost/grp dropped (no fallback)
    assert out["notice"] is False                              # a real ping -> m.text


def test_mentioned_no_known_hits_stays_silent():
    ev = Event(kind="note", action="create", room="!r", mention_logins=["ghost"])
    out = _dispatch(ev, {"mention": "mentioned", "room_id": "!r"})
    assert out["mentions"] == [] and out["notice"] is True     # no ping -> m.notice


def test_mentioned_dedupes_repeats():
    ev = Event(kind="note", action="create", room="!r",
               mention_logins=["m.bahmutskij", "m.bahmutskij"])
    out = _dispatch(ev, {"mention": "mentioned", "room_id": "!r"})
    assert out["mentions"] == ["@m.bahmutskij:fakspro.ru"]


def test_notice_false_rule_notifies_room_without_ping():
    # A comment with no @-mention still arrives as m.text (not silent m.notice)
    # when the rule pins notice=false, so the watch room gets a notification.
    ev = Event(kind="note", action="create", room="!r", mention_logins=[])
    out = _dispatch(ev, {"mention": "mentioned", "room_id": "!r", "notice": False})
    assert out["mentions"] == [] and out["notice"] is False


def test_notice_default_stays_quiet_without_ping():
    # A rule that omits `notice` keeps the old behaviour: no ping -> m.notice.
    ev = Event(kind="note", action="create", room="!r", mention_logins=[])
    out = _dispatch(ev, {"mention": "mentioned", "room_id": "!r"})
    assert out["notice"] is True


def test_assignee_mode_keeps_domain_fallback():
    # an assignee not in the users map still resolves via @login:domain (regression)
    ev = Event(kind="issue", action="open", room="!r", assignees=["d.nikulin"])
    out = _dispatch(ev, {"mention": "assignee", "room_id": "!r"})
    assert out["mentions"] == ["@d.nikulin:fakspro.ru"]


def test_thread_root_from_extra_is_passed_through():
    ev = Event(kind="note", action="create", room="!r", extra={"thread_root": "$root"})
    out, outcome = _dispatch_full(ev, {"room_id": "!r"})
    assert out["thread_root"] == "$root"              # dropped into the thread
    assert outcome["event_id"] == "$evt1"             # id surfaced for follow-ups


def test_no_thread_root_is_top_level():
    ev = Event(kind="note", action="create", room="!r")   # extra defaults to {}
    out = _dispatch(ev, {"room_id": "!r"})
    assert out["thread_root"] is None                 # a normal top-level message
