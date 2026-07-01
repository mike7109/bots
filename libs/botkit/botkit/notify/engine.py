"""Rules engine: match an Event to a rule, render it, dispatch to destinations.

A rule IS a notification type with its parameters. All behaviour lives in
config, not here:

    defaults:
      to: [room]                 # default destination(s)
      room_id: "!abc..."         # the shared room (env MATRIX_ROOM overrides)
    rules:
      - event: issue             # trigger (NB: key is `event`, not `on` — YAML
        actions: [open, close, reopen]   # reads a bare `on:` as boolean True)
        template: issue          # how it looks
        to: [room]               # where it goes: room | dm | email (or several)
        mention: assignee        # who gets pinged

A destination ("room"/"dm"/"email") maps to a transport; the transport's
`medium` ("matrix"/"email") picks the template variant `<template>.<medium>.html.j2`.
So room and dm share the same matrix template — they only differ in delivery.
"""
from __future__ import annotations

import logging
from dataclasses import asdict

from botkit.notify.event import Event

log = logging.getLogger("botkit.notify")


class Engine:
    def __init__(self, config: dict, renderer, transports: dict, identity=None,
                 settings=None, guard=None):
        self.rules = config.get("rules", [])
        self.defaults = config.get("defaults", {})
        self.renderer = renderer
        self.transports = transports          # {"matrix": MatrixTransport(...), ...}
        self.identity = identity
        self.settings = settings              # runtime settings (kill switch etc.), optional
        # SAFETY: rate-limit + circuit breaker (botkit.notify.guard.SendGuard) or
        # None. When set, every actually-delivered send is recorded here so a
        # runaway pass trips the breaker and STOPS all further sending centrally.
        self.guard = guard

    def match(self, event: Event) -> dict | None:
        """First rule whose `event:` (and optional `actions:`) matches the event."""
        for rule in self.rules:
            if rule.get("event") != event.kind:
                continue
            actions = rule.get("actions")
            if actions and event.action not in actions:
                continue
            return rule
        return None

    def _record(self, event, status, channel, detail=""):
        store = getattr(self.settings, "store", None) if self.settings is not None else None
        if store is not None:
            try:
                store.log_event(event.kind, event.action, channel, status, detail)
            except Exception:                       # noqa: BLE001 — logging must never break delivery
                log.exception("activity log write failed")

    def _guard_record(self, outcome, dest, rendered) -> None:
        """Record actually-delivered sends with the SendGuard. Best-effort —
        the guard itself is exception-safe, but never let bookkeeping break the
        handle loop. A DM outcome can deliver to several mxids; record each so a
        loop hammering one person trips the PER-TARGET cap. A room outcome (or a
        DM fallback to the shared room) records under the room id."""
        try:
            keys = []
            if isinstance(outcome, dict):
                for mxid in outcome.get("dm_to", []) or []:
                    keys.append(f"dm:{mxid}")
                room = outcome.get("room") or outcome.get("fallback_room")
                if room:
                    keys.append(str(room))
            if not keys:
                keys = [str(dest)]
            for key in keys:
                self.guard.record(key, rendered)
        except Exception:                       # noqa: BLE001 — guard must never break delivery
            log.exception("send guard record failed")

    def handle(self, event: Event) -> dict:
        # Global kill switch (admin panel): when off, the bot sends nothing.
        if self.settings is not None and not self.settings.enabled():
            self._record(event, "ignored", "", "бот выключен")
            return {"ignored": "bot disabled", "kind": event.kind, "action": event.action}

        # SAFETY breaker: if the SendGuard has tripped (a runaway pass spammed
        # past its limits), send NOTHING until an operator resets it. Checked
        # before any rule matching/dispatch so a flood is stopped centrally.
        if self.guard is not None and self.guard.blocked():
            self._record(event, "ignored", "", "предохранитель: отправка остановлена")
            return {"ignored": "breaker tripped", "kind": event.kind, "action": event.action}

        rule = self.match(event)
        if rule is None:
            return {"ignored": "no matching rule", "kind": event.kind, "action": event.action}

        # Effective on/off: a rule may ship `enabled: false` in config (present but
        # off, e.g. personal DMs); the admin override flips it without editing YAML.
        enabled = rule.get("enabled", True)
        ov = self.settings.rule_override(event.kind) if self.settings is not None else None
        if ov:
            if "enabled" in ov:
                enabled = ov["enabled"]
            if ov.get("to"):
                rule = {**rule, "to": ov["to"]}
        if not enabled:
            self._record(event, "ignored", "", "правило выключено")
            return {"ignored": "rule disabled", "kind": event.kind}

        destinations = rule.get("to") or self.defaults.get("to", ["room"])
        ctx = asdict(event)
        sent, skipped, errors = [], [], []
        event_id = None       # id of the first delivered message (for thread roots)
        for dest in destinations:
            transport = self.transports.get(dest)
            if transport is None:
                # No transport for this destination = MISCONFIG (typo'd `to:`, or
                # an enabled-but-unimplemented transport like email). This is a
                # permanent config error, NOT a transient skip — callers retry on
                # "no sent", so skipping here would silently no-op forever. Record
                # it as an error so it surfaces in the activity log / error stats.
                errors.append(dest)
                log.warning("no transport for destination %r (config error)", dest)
                self._record(event, "error", dest, f"no transport for destination '{dest}'")
                continue
            medium = getattr(transport, "medium", "matrix")
            try:
                rendered = self.renderer.render(rule["template"], medium, ctx)
                outcome = transport.dispatch(event, rule, rendered, self.defaults)
            except Exception as e:                  # noqa: BLE001 — one bad dest mustn't sink the rest
                errors.append(dest)
                log.exception("dispatch to %s failed", dest)
                self._record(event, "error", dest, f"{type(e).__name__}: {e}")
                continue
            # A transport can decline to deliver (e.g. DM with no assignee and no
            # fallback room) by returning {"skipped": ...}; don't count that as sent.
            if isinstance(outcome, dict) and "skipped" in outcome:
                skipped.append(dest)
            else:
                sent.append(dest)
                if event_id is None and isinstance(outcome, dict) and outcome.get("event_id"):
                    event_id = outcome["event_id"]
                # SAFETY: record the real delivery(s) with the guard so a runaway
                # pass trips the breaker. Recording AFTER the send means a trip
                # stops the NEXT send (this loop and all future handles). Derive a
                # target_key from the outcome so PER-TARGET caps are meaningful:
                # the room id for room sends, "dm:<mxid>" for each DM delivered;
                # fall back to the destination name. A rule may opt OUT with
                # `skip_guard` for a structural, self-bounded message (e.g. a
                # thread-anchor header, one per issue, already bounded upstream by
                # the note throttle): it is exempt from ALL guard caps (global /
                # per-target / duplicate) so it can't push any count toward a trip
                # — but it's still STOPPED by an already tripped breaker (the
                # blocked() check at the top of handle runs before any dispatch).
                if self.guard is not None and not rule.get("skip_guard"):
                    self._guard_record(outcome, dest, rendered)

        log.info("handled %s/%s -> %s", event.kind, event.action, sent)
        if sent:
            self._record(event, "sent", ",".join(sent), f"#{event.iid} {event.title}"[:200])
        elif skipped and not errors:
            self._record(event, "skipped", ",".join(skipped), f"#{event.iid} {event.title}"[:200])
        result = {"sent": sent}
        if event_id:
            result["event_id"] = event_id       # lets a caller thread follow-ups under it
        if skipped:
            result["skipped"] = skipped
        if errors:
            result["errors"] = errors
        return result
