"""Direct-message (1:1) delivery channel: send to the assignee's personal DM
instead of a shared room. Used e.g. for overdue reminders.
"""
from __future__ import annotations

from botkit.matrix import MatrixClient


class MatrixDirectTransport:
    name = "dm"          # destination key used in a rule's `to:`
    medium = "matrix"    # template variant: <template>.matrix.html.j2

    def __init__(self, client: MatrixClient, identity=None):
        self.client = client
        self.identity = identity

    def dispatch(self, event, rule: dict, rendered: str, defaults: dict) -> dict:
        # DM every assignee their own copy (1:1 room each).
        targets = []
        if self.identity:
            for login in event.assignees:
                mxid = self.identity.matrix_id(login)
                if mxid:
                    targets.append(mxid)
        if not targets:
            # No one to DM (unassigned issue) — skip rather than crash the run.
            return {"skipped": "no assignee mxid for DM"}

        for mxid in targets:
            room = self.client.get_or_create_dm(mxid)
            self.client.send_html(room, rendered, mention_user_ids=[mxid], notice=False)
        return {"dm_to": targets}
