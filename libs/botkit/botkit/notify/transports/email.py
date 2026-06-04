"""Email delivery channel — placeholder.

When you enable email: render `<template>.email.html.j2`, resolve recipients
from Identity.email(event.assignee/participants), and send via smtplib here.
Then add `email` to a rule's `channels:` — no engine/template-engine changes.
"""
from __future__ import annotations


class EmailTransport:
    name = "email"       # destination key used in a rule's `to:`
    medium = "email"     # template variant: <template>.email.html.j2

    def __init__(self, **smtp_config):
        self.smtp = smtp_config

    def dispatch(self, event, rule: dict, rendered: str, defaults: dict) -> dict:
        raise NotImplementedError("email transport not implemented yet — see module docstring")
