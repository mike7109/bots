"""The canonical event every bot normalizes its payload into.

Channels and templates only ever see this shape, so a new source (another
GitLab event, a cron poll, a different system entirely) just needs to produce
an `Event` — nothing downstream changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Event:
    kind: str                       # issue | task | due_soon | ... (matched against rule `on:`)
    action: str                     # open | close | reopen | due | ...
    project: str = ""               # path_with_namespace, e.g. "fakspro/infra"
    iid: str = ""                   # issue/task number within the project
    title: str = ""
    url: str = ""
    state: str = ""                 # opened | closed | ...
    labels: list[str] = field(default_factory=list)
    assignee: str | None = None     # GitLab login (resolved to Matrix/email via Identity)
    author: str | None = None       # GitLab login of who triggered it
    due: str | None = None          # ISO date, for due_soon
    extra: dict = field(default_factory=dict)  # anything source-specific
