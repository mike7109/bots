"""Operator alerting: DM designated engineers when the bot itself fails.

A scheduled pass that throws (expired Matrix token, GitLab unreachable, a bug)
is otherwise silent — the bot just stops doing its job and nobody notices for
days. The Alerter turns such a failure into a direct message to the people
listed as "engineers" in the admin panel, so a silent multi-day outage gets
caught.

INHERENT LIMITATION: if Matrix itself is the thing that's down (expired bot
token, homeserver unreachable), the alert DM can't get through either — it goes
out the same broken pipe. For that case the backstop is the dashboard: the red
error banner (delivery errors today) plus the activity log surface the failure
to anyone who opens the panel. The DM alert covers the common case where the
bot can still reach Matrix but a *pass* (e.g. GitLab access) failed.

Throttling: alerts are deduped through the same one-shot ledger the digests use
(kind="alert"), keyed per failure per day. A pass that retries every 30s thus
DMs the engineers at most once per day per failing pass — never a storm.
"""
from __future__ import annotations

import datetime as dt
import logging

from markupsafe import escape

log = logging.getLogger("gitlab-notify.alerts")


class Alerter:
    def __init__(self, matrix, identity, settings, store):
        self.matrix = matrix
        self.identity = identity
        self.settings = settings
        self.store = store

    @staticmethod
    def _today() -> str:
        return dt.date.today().isoformat()

    def _recipients(self) -> list[str]:
        """Resolve configured engineer logins to mxids, dropping unresolvable."""
        logins = self.settings.get_alerts().get("engineers", [])
        out = []
        for login in logins:
            mxid = self.identity.matrix_id(login)
            if mxid:
                out.append(mxid)
        return out

    def alert(self, subject: str, detail: str, *, key: str, day: str | None = None) -> bool:
        """DM the engineers about a bot failure. Returns True iff at least one
        DM was delivered (and the throttle was burned for this key/day).

        No-ops (return False) when: alerting is disabled, this key was already
        alerted today (throttle), there are no resolvable recipients, or every
        DM attempt failed (so the throttle is NOT burned and it can retry)."""
        day = day or self._today()
        alerts = self.settings.get_alerts()
        if not alerts.get("enabled", True):
            return False
        if self.store.already_sent("alert", key, day=day):
            return False                              # already alerted today
        recipients = self._recipients()
        if not recipients:
            return False                              # nobody to tell
        # Subject/detail carry raw error text — escape before embedding in HTML.
        html = (f"⚠️ <b>gitlab-notify</b> — {escape(subject)}<br>{escape(detail)}")
        delivered = False
        for mxid in recipients:
            try:
                dm = self.matrix.get_or_create_dm(mxid)
                self.matrix.send_html(dm, html, mention_user_ids=[mxid], notice=False)
                delivered = True
            except Exception as e:                    # noqa: BLE001 — one bad DM mustn't stop the rest
                log.warning("alert DM to %s failed: %s", mxid, e)
        if delivered:
            self.store.mark_sent("alert", key, day=day)   # burn throttle only on success
            return True
        return False                                  # total failure -> retry next tick

    def alert_pass_failure(self, source_id: str, pass_name: str, exc: Exception, *, day: str) -> bool:
        """Convenience wrapper for a scheduled-pass exception."""
        subject = f"рассылка «{pass_name}» ({source_id}) упала"
        detail = f"{type(exc).__name__}: {exc}"
        return self.alert(subject, detail, key=f"{source_id}:{pass_name}", day=day)
