"""Direct-message (1:1) delivery channel: send to the assignee's personal DM
instead of a shared room. Used e.g. for overdue reminders.
"""
from __future__ import annotations

from botkit.matrix import MatrixClient


class MatrixDirectTransport:
    name = "dm"          # destination key used in a rule's `to:`
    medium = "matrix"    # template variant: <template>.matrix.html.j2

    def __init__(self, client: MatrixClient, identity=None, settings=None):
        self.client = client
        self.identity = identity
        self.settings = settings    # per-user mute / push mode, optional

    def dispatch(self, event, rule: dict, rendered: str, defaults: dict) -> dict:
        # (login, mxid) so per-user prefs (mute/push) can be applied below.
        targets, muted = [], []
        if self.identity:
            for login in event.assignees:
                if self.settings is not None and self.settings.is_muted(login):
                    muted.append(login)
                    continue
                mxid = self.identity.matrix_id(login)
                if mxid:
                    targets.append((login, mxid))

        room = getattr(event, "room", None) or defaults.get("room_id")   # source room first
        if not targets:
            if muted:
                # Everyone targeted is muted — deliberately silent, not a failure.
                return {"skipped": "muted", "muted": muted}
            # No resolvable assignee (unassigned, or no mxid mapping) — fall back
            # to the shared room so it isn't lost; nobody to ping there.
            if room:
                self.client.send_html(room, rendered, notice=True)
                return {"fallback_room": room}
            return {"skipped": "no assignee and no room_id"}

        delivered, pending = [], []
        for login, mxid in targets:
            dm = self.client.get_or_create_dm(mxid)   # creates + invites once, then reuses
            # A DM only reaches someone who accepted the invite. If they haven't
            # (still "invite"), do NOT leak personal content into the shared room
            # — just note them as pending and retry next run. The standing invite
            # is enough; once accepted, the next digest lands in their DM.
            if self.client.membership(dm, mxid) == "join":
                notice = self.settings.push_notice(login, False) if self.settings else False
                self.client.send_html(dm, rendered, mention_user_ids=[mxid], notice=notice)
                delivered.append(mxid)
            else:
                pending.append(mxid)

        result = {}
        if delivered:
            result["dm_to"] = delivered
        if pending:
            result["pending_invite"] = pending
        if muted:
            result["muted"] = muted
        if not delivered:
            # Nothing actually reached anyone — report skipped so the caller
            # doesn't mark it sent, and tries again next run.
            result["skipped"] = "no DM delivered (pending invites / muted)"
        return result
