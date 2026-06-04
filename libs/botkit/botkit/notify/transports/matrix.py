"""Matrix delivery channel for the notification engine."""
from __future__ import annotations

from botkit.matrix import MatrixClient


class MatrixTransport:
    name = "room"        # destination key used in a rule's `to:`
    medium = "matrix"    # template variant: <template>.matrix.html.j2

    def __init__(self, client: MatrixClient, identity=None):
        self.client = client
        self.identity = identity

    def dispatch(self, event, rule: dict, rendered: str, defaults: dict) -> dict:
        room = rule.get("room_id") or defaults.get("room_id")
        if not room:
            raise RuntimeError("room transport: no room_id (set env MATRIX_ROOM or defaults.room_id)")

        # `mention: assignee` in the rule => actually ping every assignee.
        mentions: list[str] = []
        if rule.get("mention") == "assignee" and self.identity:
            for login in event.assignees:
                mxid = self.identity.matrix_id(login)
                if mxid:
                    mentions.append(mxid)

        self.client.send_html(
            room, rendered,
            mention_user_ids=mentions,
            notice=not mentions,      # m.text when pinging so clients don't suppress the highlight
        )
        return {"room": room, "mentions": mentions}
