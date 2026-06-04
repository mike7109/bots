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

        destinations = rule.get("to") or self.defaults.get("to", ["room"])
        ctx = asdict(event)
        sent, skipped = [], []
        for dest in destinations:
            transport = self.transports.get(dest)
            if transport is None:
                skipped.append(dest)
                continue
            medium = getattr(transport, "medium", "matrix")
            rendered = self.renderer.render(rule["template"], medium, ctx)
            transport.dispatch(event, rule, rendered, self.defaults)
            sent.append(dest)

        log.info("handled %s/%s -> %s", event.kind, event.action, sent)
        result = {"sent": sent}
        if skipped:
            result["skipped_unconfigured"] = skipped
        return result
