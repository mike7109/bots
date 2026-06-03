"""Jinja2 renderer. Templates are named `<template>.<channel>.<fmt>.j2`,
e.g. `issue.matrix.html.j2` — so the same logical message can have a distinct
look per channel (Matrix HTML vs. email HTML) without touching code.
"""
from __future__ import annotations

import os

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

# Human-readable action verbs (Russian) for templates: `{{ action | ru_action }}`.
_RU_ACTION = {
    "open": "открыта",
    "close": "закрыта",
    "reopen": "переоткрыта",
    "due": "истекает завтра",
}


def _labels_html(labels) -> Markup:
    """Render GitLab labels as little 🏷 chips (escaped)."""
    if not labels:
        return Markup("")
    return Markup(" ".join(f"🏷 {escape(name)}" for name in labels))


class Renderer:
    def __init__(self, templates_dir: str | os.PathLike, identity=None):
        self.env = Environment(
            loader=FileSystemLoader(str(templates_dir)),
            autoescape=select_autoescape(enabled_extensions=("j2", "html"), default=True),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        self.env.globals["labels_html"] = _labels_html
        self.env.filters["ru_action"] = lambda action: _RU_ACTION.get(action, action)
        # `{{ mention(assignee) }}` -> clickable Matrix pill (or plain text).
        if identity is not None:
            self.env.globals["mention"] = identity.matrix_pill
        else:
            self.env.globals["mention"] = lambda login: Markup("")

    def render(self, template: str, channel: str, ctx: dict, fmt: str = "html") -> str:
        tmpl = self.env.get_template(f"{template}.{channel}.{fmt}.j2")
        return tmpl.render(**ctx).strip()
