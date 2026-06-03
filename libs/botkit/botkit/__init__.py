"""botkit — shared building blocks for the bots in this monorepo.

Each bot (under ../../services/) imports what it needs from here instead of
reimplementing Matrix/GitLab access, identity mapping, or message rendering.
"""

__all__ = ["config", "matrix", "gitlab", "identity", "webhook", "notify"]
