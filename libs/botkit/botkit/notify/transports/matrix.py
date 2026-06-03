"""Matrix delivery channel for the notification engine."""
from __future__ import annotations

from botkit.matrix import MatrixClient


class MatrixTransport:
    name = "matrix"

    def __init__(self, client: MatrixClient, identity=None):
        self.client = client
        self.identity = identity

    def dispatch(self, event, rule: dict, rendered: str, defaults: dict) -> dict:
        room = (rule.get("matrix") or {}).get("room") \
            or (defaults.get("matrix") or {}).get("room")
        if not room:
            raise RuntimeError("matrix transport: no room configured (rule.matrix.room / defaults.matrix.room)")

        # `mention: assignee` in the rule => actually ping that person.
        mentions: list[str] = []
        if rule.get("mention") == "assignee" and event.assignee and self.identity:
            mxid = self.identity.matrix_id(event.assignee)
            if mxid:
                mentions.append(mxid)

        self.client.send_html(
            room, rendered,
            mention_user_ids=mentions,
            notice=not mentions,      # m.text when pinging so clients don't suppress the highlight
        )
        return {"room": room, "mentions": mentions}
