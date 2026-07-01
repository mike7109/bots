"""Map a GitLab username to its Matrix / email identities and display name.

Configured via the `users:` block in a bot's config.yaml, e.g.

    users:
      m.bahmutskij: { matrix: "@m.bahmutskij:fakspro.ru", name: "Михаил Бахмутский", email: "..." }
    matrix_domain: fakspro.ru   # fallback: @<login>:<domain>
"""
from __future__ import annotations

from markupsafe import Markup, escape


class Identity:
    def __init__(self, users: dict | None = None, *, matrix_domain: str | None = None):
        self._users = users or {}
        self._domain = matrix_domain

    def matrix_id(self, login: str | None) -> str | None:
        if not login:
            return None
        user = self._users.get(login) or {}
        if user.get("matrix"):
            return user["matrix"]
        if self._domain:
            return f"@{login}:{self._domain}"
        return None

    def known_matrix_id(self, login: str | None) -> str | None:
        """Configured mxid for a login, WITHOUT the @login:domain fallback — so an
        unknown/typo'd/group @mention resolves to None (no ghost ping)."""
        if not login:
            return None
        return (self._users.get(login) or {}).get("matrix")

    def email(self, login: str | None) -> str | None:
        if not login:
            return None
        return (self._users.get(login) or {}).get("email")

    def display_name(self, login: str | None) -> str:
        if not login:
            return ""
        return (self._users.get(login) or {}).get("name") or login

    def matrix_pill(self, login: str | None) -> Markup:
        """Render a clickable Matrix mention pill (escaped). Empty if no login."""
        if not login:
            return Markup("")
        mxid = self.matrix_id(login)
        name = escape(self.display_name(login))
        if not mxid:
            return Markup(name)
        return Markup(f'<a href="https://matrix.to/#/{escape(mxid)}">{name}</a>')
