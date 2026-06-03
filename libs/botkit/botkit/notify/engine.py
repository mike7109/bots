"""Rules engine: match an Event to a rule, render it, dispatch to transports.

All behaviour lives in config, not here:

    defaults:
      channels: [matrix]
      matrix: { room: "!abc..." }
    rules:
      - event: issue                 # NB: keep this key `event`, not `on` —
        actions: [open, close, reopen]  # YAML parses a bare `on:` key as the
        template: issue                 # boolean True (the "Norway problem").
        mention: assignee
        # channels: [matrix, email]   # override per rule
"""
from __future__ import annotations

import logging
from dataclasses import asdict

from botkit.notify.event import Event

log = logging.getLogger("botkit.notify")


class Engine:
    def __init__(self, config: dict, renderer, transports: dict, identity=None):
        self.rules = config.get("rules", [])
        self.defaults = config.get("defaults", {})
        self.renderer = renderer
        self.transports = transports          # {"matrix": MatrixTransport(...), ...}
        self.identity = identity

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

    def handle(self, event: Event) -> dict:
        rule = self.match(event)
        if rule is None:
            return {"ignored": "no matching rule", "kind": event.kind, "action": event.action}

        channels = rule.get("channels") or self.defaults.get("channels", ["matrix"])
        ctx = asdict(event)
        sent, skipped = [], []
        for channel in channels:
            transport = self.transports.get(channel)
            if transport is None:
                skipped.append(channel)
                continue
            rendered = self.renderer.render(rule["template"], channel, ctx)
            transport.dispatch(event, rule, rendered, self.defaults)
            sent.append(channel)

        log.info("handled %s/%s -> %s", event.kind, event.action, sent)
        result = {"sent": sent}
        if skipped:
            result["skipped_unconfigured"] = skipped
        return result
