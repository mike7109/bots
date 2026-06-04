"""Minimal Matrix client-server API client: send formatted messages to a room.

Reused by every bot that talks to Matrix. Keeps no state beyond the auth
token, so a single instance is safe to share across requests.
"""
from __future__ import annotations

import re
import uuid
from collections.abc import Iterable

import requests

_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(html: str) -> str:
    """Crude HTML -> plaintext for the `body` fallback (non-HTML clients)."""
    text = html.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    return _TAG_RE.sub("", text).strip()


class MatrixClient:
    def __init__(self, homeserver: str, token: str, *, timeout: float = 10.0):
        self.base = homeserver.rstrip("/")
        self.timeout = timeout
        self._s = requests.Session()
        self._s.headers["Authorization"] = f"Bearer {token}"
        self._user_id: str | None = None

    def send_html(
        self,
        room_id: str,
        html: str,
        *,
        plain: str | None = None,
        mention_user_ids: Iterable[str] | None = None,
        notice: bool = True,
    ) -> str | None:
        """Send a formatted message. Returns the new event id.

        `notice=True` uses m.notice (the convention for bot output). When you
        actually want to ping people, pass their mxids in `mention_user_ids`
        and set `notice=False` so clients don't suppress the highlight.
        """
        mentions = [m for m in (mention_user_ids or []) if m]
        content: dict = {
            "msgtype": "m.notice" if notice else "m.text",
            "body": plain if plain is not None else _strip_html(html),
            "format": "org.matrix.custom.html",
            "formatted_body": html,
        }
        if mentions:
            content["m.mentions"] = {"user_ids": mentions}

        room = requests.utils.quote(room_id, safe="")
        txn = uuid.uuid4().hex
        url = f"{self.base}/_matrix/client/v3/rooms/{room}/send/m.room.message/{txn}"
        resp = self._s.put(url, json=content, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json().get("event_id")

    # --- direct messages -------------------------------------------------
    @property
    def user_id(self) -> str:
        """The bot's own mxid (cached). Needed to read/write its account data."""
        if self._user_id is None:
            resp = self._s.get(
                f"{self.base}/_matrix/client/v3/account/whoami", timeout=self.timeout
            )
            resp.raise_for_status()
            self._user_id = resp.json()["user_id"]
        return self._user_id

    def _direct_map(self) -> dict:
        """The bot's `m.direct` account data: {user_id: [dm_room_ids]}."""
        me = requests.utils.quote(self.user_id, safe="")
        url = f"{self.base}/_matrix/client/v3/user/{me}/account_data/m.direct"
        resp = self._s.get(url, timeout=self.timeout)
        if resp.status_code == 404:
            return {}
        resp.raise_for_status()
        return resp.json()

    def get_or_create_dm(self, user_id: str) -> str:
        """Return a 1:1 room with `user_id`, reusing an existing DM if there is
        one (tracked in m.direct), else creating it and inviting them.

        First message arrives as a DM invite the person accepts once; after
        that the room is reused silently.
        """
        direct = self._direct_map()
        existing = direct.get(user_id) or []
        if existing:
            return existing[0]

        resp = self._s.post(
            f"{self.base}/_matrix/client/v3/createRoom",
            json={"preset": "trusted_private_chat", "is_direct": True, "invite": [user_id]},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        room_id = resp.json()["room_id"]

        direct.setdefault(user_id, []).append(room_id)
        me = requests.utils.quote(self.user_id, safe="")
        self._s.put(
            f"{self.base}/_matrix/client/v3/user/{me}/account_data/m.direct",
            json=direct,
            timeout=self.timeout,
        ).raise_for_status()
        return room_id
