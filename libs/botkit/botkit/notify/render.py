"""Jinja2 renderer. Templates are named `<template>.<channel>.<fmt>.j2`,
e.g. `issue.matrix.html.j2` — so the same logical message can have a distinct
look per channel (Matrix HTML vs. email HTML) without touching code.

Template helpers available:
  {{ action | action_emoji }}   🟢 / 🔴 / 🔁 / 📅 / ⏰
  {{ action | ru_action }}      Открыта / Закрыта / Переоткрыта / …
  {{ due | ru_date }}           "2026-06-05" -> "5 июня"
  {{ meta(labels, assignee) }}  " · 🏷 chips · @pill" (empty if both empty)
  {{ mention(login) }}          a single @pill
  {{ labels_html(labels) }}     🏷 chips
"""
from __future__ import annotations

import os

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

# Action -> bold verb / leading status emoji used by templates.
_RU_ACTION = {
    "open": "Открыта",
    "close": "Закрыта",
    "reopen": "Переоткрыта",
    "due": "Дедлайн завтра",
    "overdue": "Просрочено",
}
_ACTION_EMOJI = {
    "open": "🟢", "close": "🔴", "reopen": "🔁", "due": "📅", "overdue": "⏰",
}
_RU_MONTHS = ["", "января", "февраля", "марта", "апреля", "мая", "июня",
              "июля", "августа", "сентября", "октября", "ноября", "декабря"]


def _labels_html(labels) -> Markup:
    """Render GitLab labels as little 🏷 chips (escaped)."""
    if not labels:
        return Markup("")
    return Markup(" ".join(f"🏷 {escape(name)}" for name in labels))


def _ru_date(iso) -> str:
    """'2026-06-05' -> '5 июня'. Falls back to the raw value on any surprise."""
    try:
        _, month, day = str(iso).split("-")
        return f"{int(day)} {_RU_MONTHS[int(month)]}"
    except Exception:                                  # noqa: BLE001
        return iso or ""


def _make_meta(pill):
    """A ` · 🏷 chips · @pill` suffix, omitting whichever parts are empty.
    Returns "" when there are neither labels nor an assignee."""
    def meta(labels=None, assignee=None) -> Markup:
        parts = []
        chips = _labels_html(labels)
        if chips:
            parts.append(str(chips))
        if assignee:
            who = pill(assignee)
            if who:
                parts.append(str(who))
        return Markup(" · " + " · ".join(parts)) if parts else Markup("")
    return meta


class Renderer:
    def __init__(self, templates_dir: str | os.PathLike, identity=None):
        self.env = Environment(
            loader=FileSystemLoader(str(templates_dir)),
            autoescape=select_autoescape(enabled_extensions=("j2", "html"), default=True),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        pill = identity.matrix_pill if identity is not None else (lambda login: Markup(""))

        self.env.globals["labels_html"] = _labels_html
        self.env.globals["mention"] = pill
        self.env.globals["meta"] = _make_meta(pill)
        self.env.filters["ru_action"] = lambda action: _RU_ACTION.get(action, action)
        self.env.filters["action_emoji"] = lambda action: _ACTION_EMOJI.get(action, "•")
        self.env.filters["ru_date"] = _ru_date

    def render(self, template: str, channel: str, ctx: dict, fmt: str = "html") -> str:
        tmpl = self.env.get_template(f"{template}.{channel}.{fmt}.j2")
        return tmpl.render(**ctx).strip()
