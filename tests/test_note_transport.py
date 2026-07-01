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

    def send_html(self, room, html, mention_user_ids=None, notice=True):
        self.sent.append({"room": room, "html": html,
                          "mentions": list(mention_user_ids or []), "notice": notice})


def _dispatch(event, rule):
    client = FakeClient()
    MatrixTransport(client, identity=IDENTITY).dispatch(event, rule, "<b>hi</b>", {})
    return client.sent[0]


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


def test_assignee_mode_keeps_domain_fallback():
    # an assignee not in the users map still resolves via @login:domain (regression)
    ev = Event(kind="issue", action="open", room="!r", assignees=["d.nikulin"])
    out = _dispatch(ev, {"mention": "assignee", "room_id": "!r"})
    assert out["mentions"] == ["@d.nikulin:fakspro.ru"]
