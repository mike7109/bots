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


def _make_mentions(pill):
    """Natural-language list of @pills:
    1 -> "@A", 2 -> "@A и @B", 3+ -> "@A, @B и @C"."""
    def mentions(logins) -> Markup:
        pills = [p for p in (str(pill(l)) for l in (logins or []) if l) if p]
        if not pills:
            return Markup("")
        if len(pills) == 1:
            return Markup(pills[0])
        return Markup(", ".join(pills[:-1]) + " и " + pills[-1])
    return mentions


def _make_meta(mentions):
    """A ` · 🏷 chips · @who` suffix, omitting whichever parts are empty.
    `assignees` is a list of logins. Returns "" when nothing to show."""
    def meta(labels=None, assignees=None) -> Markup:
        parts = []
        chips = _labels_html(labels)
        if chips:
            parts.append(str(chips))
        who = mentions(assignees)
        if who:
            parts.append(str(who))
        return Markup(" · " + " · ".join(parts)) if parts else Markup("")
    return meta


def _make_who(mentions):
    """A ` · @A и @B` assignees suffix (leading separator), "" if none."""
    def who(assignees=None) -> Markup:
        names = mentions(assignees)
        return Markup(" · " + str(names)) if names else Markup("")
    return who


class Renderer:
    def __init__(self, templates_dir: str | os.PathLike, identity=None):
        self.env = Environment(
            loader=FileSystemLoader(str(templates_dir)),
            autoescape=select_autoescape(enabled_extensions=("j2", "html"), default=True),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        pill = identity.matrix_pill if identity is not None else (lambda login: Markup(""))
        mentions = _make_mentions(pill)

        self.env.globals["labels_html"] = _labels_html
        self.env.globals["mention"] = pill          # single @pill
        self.env.globals["mentions"] = mentions     # natural list of @pills
        self.env.globals["meta"] = _make_meta(mentions)   # " · 🏷 chips · @who"
        self.env.globals["who"] = _make_who(mentions)     # " · @who" (assignees only)
        self.env.filters["ru_action"] = lambda action: _RU_ACTION.get(action, action)
        self.env.filters["action_emoji"] = lambda action: _ACTION_EMOJI.get(action, "•")
        self.env.filters["ru_date"] = _ru_date

    def render(self, template: str, channel: str, ctx: dict, fmt: str = "html") -> str:
        tmpl = self.env.get_template(f"{template}.{channel}.{fmt}.j2")
        return tmpl.render(**ctx).strip()
