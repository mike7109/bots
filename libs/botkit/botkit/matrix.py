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
