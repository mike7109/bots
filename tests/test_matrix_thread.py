"""MatrixClient.send_html wire format: msgtype, m.mentions, and the m.thread
relation that makes clients nest a message under a thread root."""
from __future__ import annotations

from botkit.matrix import MatrixClient


class FakeResp:
    def raise_for_status(self):
        pass

    def json(self):
        return {"event_id": "$e"}


class FakeSession:
    def __init__(self):
        self.headers = {}
        self.last = None

    def put(self, url, json=None, timeout=None):
        self.last = json
        return FakeResp()


def _client():
    c = MatrixClient("https://hs", "tok")
    c._s = FakeSession()
    return c


def test_plain_message_has_no_thread_relation():
    c = _client()
    c.send_html("!r", "<b>hi</b>")
    assert "m.relates_to" not in c._s.last
    assert c._s.last["msgtype"] == "m.notice"          # default is a quiet notice


def test_thread_root_builds_m_thread_relation():
    c = _client()
    eid = c.send_html("!r", "<b>hi</b>", notice=False, thread_root="$root")
    rel = c._s.last["m.relates_to"]
    assert rel["rel_type"] == "m.thread"
    assert rel["event_id"] == "$root"
    assert rel["is_falling_back"] is True
    assert rel["m.in_reply_to"] == {"event_id": "$root"}   # reply fallback for non-thread clients
    assert c._s.last["msgtype"] == "m.text"
    assert eid == "$e"


def test_mentions_land_in_m_mentions():
    c = _client()
    c.send_html("!r", "hi", mention_user_ids=["@a:s"], notice=False)
    assert c._s.last["m.mentions"] == {"user_ids": ["@a:s"]}


def test_edit_html_builds_m_replace():
    # edit_html replaces a sent message's content in place: m.new_content carries
    # the real content, the top level the spec's `* ` fallback, and m.relates_to
    # points at the ORIGINAL event (this is how a thread root gets renamed).
    c = _client()
    eid = c.edit_html("!r", "$root", "<b>новое название</b>")
    last = c._s.last
    assert last["m.relates_to"] == {"rel_type": "m.replace", "event_id": "$root"}
    assert last["m.new_content"]["formatted_body"] == "<b>новое название</b>"
    assert last["m.new_content"]["msgtype"] == "m.notice"
    assert last["body"].startswith("* ")                  # fallback for non-edit clients
    assert last["formatted_body"].startswith("* ")
    assert eid == "$e"
