"""_invite_status: the recipients list merges config users with the bot's actual
DM partners (m.direct), so people the bot messages but who aren't in config.yaml
still show up (keyed by their mxid localpart, flagged external)."""
from __future__ import annotations

from botkit.identity import Identity

from admin import _invite_status


class FakeMatrix:
    def __init__(self, direct, joined=(), fail=False):
        self._direct = direct
        self._joined = set(joined)
        self._fail = fail

    def _direct_map(self):
        if self._fail:
            raise RuntimeError("m.direct unavailable")
        return self._direct

    def membership(self, room, mxid):
        return "join" if mxid in self._joined else "invite"


class Ctx:
    def __init__(self, config, identity, matrix):
        self.config = config
        self.identity = identity
        self.matrix = matrix


def _ctx(direct, *, joined=(), fail=False):
    cfg = {"users": {"bob": {"name": "Bob", "matrix": "@bob:srv"}}}
    ident = Identity(cfg["users"], matrix_domain="srv")
    return Ctx(cfg, ident, FakeMatrix(direct, joined=joined, fail=fail))


def test_config_user_accepted():
    out = _invite_status(_ctx({"@bob:srv": ["!r1"]}, joined=["@bob:srv"]))
    assert out["bob"]["external"] is False
    assert out["bob"]["invite"] == "accepted"
    assert out["bob"]["mxid"] == "@bob:srv"


def test_extra_dm_partner_shown_as_external():
    out = _invite_status(_ctx({"@bob:srv": ["!r1"], "@ext.user:srv": ["!r2"]},
                              joined=["@bob:srv"]))
    assert "ext.user" in out                       # keyed by mxid localpart
    assert out["ext.user"]["external"] is True
    assert out["ext.user"]["mxid"] == "@ext.user:srv"
    assert out["ext.user"]["invite"] == "pending"  # has a DM room but hasn't joined


def test_extra_partner_accepted():
    out = _invite_status(_ctx({"@ext.user:srv": ["!r2"]}, joined=["@ext.user:srv"]))
    assert out["ext.user"]["invite"] == "accepted"


def test_no_direct_map_degrades_gracefully():
    out = _invite_status(_ctx({}, fail=True))
    assert out["bob"]["invite"] == "none"
    assert out["bob"]["external"] is False
    assert all(not u["external"] for u in out.values())   # no extras when m.direct fails
