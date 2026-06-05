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
    def __init__(self, config: dict, renderer, transports: dict, identity=None, settings=None):
        self.rules = config.get("rules", [])
        self.defaults = config.get("defaults", {})
        self.renderer = renderer
        self.transports = transports          # {"matrix": MatrixTransport(...), ...}
        self.identity = identity
        self.settings = settings              # runtime settings (kill switch etc.), optional

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

    def handle(self, event: Event) -> dict:
        # Global kill switch (admin panel): when off, the bot sends nothing.
        if self.settings is not None and not self.settings.enabled():
            self._record(event, "ignored", "", "бот выключен")
            return {"ignored": "bot disabled", "kind": event.kind, "action": event.action}

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
        for dest in destinations:
            transport = self.transports.get(dest)
            if transport is None:
                skipped.append(dest)
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

        log.info("handled %s/%s -> %s", event.kind, event.action, sent)
        if sent:
            self._record(event, "sent", ",".join(sent), f"#{event.iid} {event.title}"[:200])
        elif skipped and not errors:
            self._record(event, "skipped", ",".join(skipped), f"#{event.iid} {event.title}"[:200])
        result = {"sent": sent}
        if skipped:
            result["skipped"] = skipped
        if errors:
            result["errors"] = errors
        return result
