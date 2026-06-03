"""Config & environment helpers shared by all bots."""
from __future__ import annotations

import os
from pathlib import Path

import yaml


def env(name: str, default: str | None = None, *, required: bool = False) -> str | None:
    """Read an env var. With ``required=True`` raise instead of returning empty.

    Bots call this at import time so misconfiguration fails fast at startup
    rather than 500-ing on the first request.
    """
    val = os.environ.get(name, default)
    if required and not val:
        raise RuntimeError(f"missing required env var: {name}")
    return val


def load_yaml(path: str | os.PathLike) -> dict:
    """Load a YAML file into a dict (empty dict if the file is empty)."""
    return yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
