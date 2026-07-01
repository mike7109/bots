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
        # event.room (per-source routing) wins, then a rule-pinned room, then default.
        room = getattr(event, "room", None) or rule.get("room_id") or defaults.get("room_id")
        if not room:
            raise RuntimeError("room transport: no room_id (set env MATRIX_ROOM or defaults.room_id)")

        # Ping recipients per the rule's mention mode:
        #   assignee  -> event.assignees, resolved WITH the @login:domain fallback
        #                (assignees are real GitLab users).
        #   mentioned -> event.mention_logins, resolved WITHOUT fallback so a typo'd
        #                or group @mention can't ping a ghost mxid and flip the whole
        #                message to a loud highlight.
        mentions: list[str] = []
        if self.identity:
            mode = rule.get("mention")
            logins, resolve = [], None
            if mode == "assignee":
                logins, resolve = event.assignees, self.identity.matrix_id
            elif mode == "mentioned":
                logins, resolve = event.mention_logins, self.identity.known_matrix_id
            for login in logins:
                mxid = resolve(login) if resolve else None
                if mxid and mxid not in mentions:
                    mentions.append(mxid)

        self.client.send_html(
            room, rendered,
            mention_user_ids=mentions,
            notice=not mentions,      # m.text when pinging so clients don't suppress the highlight
        )
        return {"room": room, "mentions": mentions}
