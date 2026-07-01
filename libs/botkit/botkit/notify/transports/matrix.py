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

        # Delivery loudness. Default: m.text only when actually pinging, else
        # m.notice (quiet) — so clients don't suppress a highlight, but routine
        # bot output stays low-key. A rule may pin it with `notice: true|false`;
        # watched-issue comments set `notice: false` so the room is NOTIFIED about
        # a new comment even with nobody @-mentioned (m.notice is suppressed by
        # clients' default push rules, which reads as "arrived silently").
        notice = rule.get("notice")
        if notice is None:
            notice = not mentions
        # Optional threading: a caller can set event.extra["thread_root"] to drop
        # this message into an existing thread (e.g. all comments of one issue
        # under the first). Absent -> a normal top-level message (unchanged).
        thread_root = (getattr(event, "extra", None) or {}).get("thread_root")
        event_id = self.client.send_html(
            room, rendered,
            mention_user_ids=mentions,
            notice=notice,
            thread_root=thread_root,
        )
        return {"room": room, "mentions": mentions, "event_id": event_id}
