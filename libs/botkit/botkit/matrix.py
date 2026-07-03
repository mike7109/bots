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
        self.base = (homeserver or "").rstrip("/")
        self.timeout = timeout
        self._s = requests.Session()
        self._s.headers["Authorization"] = f"Bearer {token}"
        self._user_id: str | None = None

    def reconfigure(self, homeserver: str | None = None, token: str | None = None) -> None:
        """Swap the homeserver/token at runtime (admin panel) — re-resolves whoami."""
        if homeserver:
            self.base = homeserver.rstrip("/")
        if token:
            self._s.headers["Authorization"] = f"Bearer {token}"
        self._user_id = None

    def send_html(
        self,
        room_id: str,
        html: str,
        *,
        plain: str | None = None,
        mention_user_ids: Iterable[str] | None = None,
        notice: bool = True,
        thread_root: str | None = None,
    ) -> str | None:
        """Send a formatted message. Returns the new event id.

        `notice=True` uses m.notice (the convention for bot output). When you
        actually want to ping people, pass their mxids in `mention_user_ids`
        and set `notice=False` so clients don't suppress the highlight.

        `thread_root` is the event id of a thread's root event: pass it to drop
        this message into that thread (m.thread relation). The reply-fallback
        points at the root so non-threaded clients still show it as a reply.
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
        if thread_root:
            content["m.relates_to"] = {
                "rel_type": "m.thread",
                "event_id": thread_root,
                "is_falling_back": True,
                "m.in_reply_to": {"event_id": thread_root},
            }

        room = requests.utils.quote(room_id, safe="")
        txn = uuid.uuid4().hex
        url = f"{self.base}/_matrix/client/v3/rooms/{room}/send/m.room.message/{txn}"
        resp = self._s.put(url, json=content, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json().get("event_id")

    def edit_html(
        self,
        room_id: str,
        target_event_id: str,
        html: str,
        *,
        plain: str | None = None,
        notice: bool = True,
    ) -> str | None:
        """Replace a previously sent message's content in place (m.replace).

        Clients re-render the ORIGINAL event with the new content — nothing new
        appears in the timeline. Editing a thread's ROOT event renames the
        thread (Element titles a topic by its root's content). Only works on
        the bot's own messages. The top-level body carries the spec's `* `
        fallback prefix for clients that don't understand edits.
        """
        body = plain if plain is not None else _strip_html(html)
        new_content: dict = {
            "msgtype": "m.notice" if notice else "m.text",
            "body": body,
            "format": "org.matrix.custom.html",
            "formatted_body": html,
        }
        content = {
            **new_content,
            "body": f"* {body}",
            "formatted_body": f"* {html}",
            "m.new_content": new_content,
            "m.relates_to": {"rel_type": "m.replace", "event_id": target_event_id},
        }
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

    def membership(self, room_id: str, user_id: str) -> str | None:
        """A user's membership in a room: 'join' | 'invite' | 'leave' | None.

        Used to tell "they accepted the DM invite" (join) from "still pending"
        (invite) so a DM that won't be seen can fall back to the shared room.
        """
        room = requests.utils.quote(room_id, safe="")
        user = requests.utils.quote(user_id, safe="")
        resp = self._s.get(
            f"{self.base}/_matrix/client/v3/rooms/{room}/state/m.room.member/{user}",
            timeout=self.timeout,
        )
        if resp.status_code in (403, 404):
            return None
        resp.raise_for_status()
        return resp.json().get("membership")

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
