"""Shared wiring for the webhook app and the cron job.

Builds the notification engine from config.yaml + env so both entrypoints
(app.py, cron.py) deliver through exactly the same rules/templates/transports.
"""
from __future__ import annotations

from pathlib import Path

from botkit.config import env, load_yaml
from botkit.identity import Identity
from botkit.matrix import MatrixClient
from botkit.notify.engine import Engine
from botkit.notify.render import Renderer
from botkit.notify.transports.matrix import MatrixTransport
from botkit.notify.transports.matrix_direct import MatrixDirectTransport
from botkit.store import Store

import digests
from settings import Settings

HERE = Path(__file__).parent


def _schedule_defaults() -> dict:
    """Seed schedule from env; the admin panel overrides these in the DB."""
    return {
        "anchor_days": sorted(digests.parse_days(env("DIGEST_ANCHOR_DAYS", "wed,fri"))),
        "weekly_day": digests.parse_day(env("DIGEST_WEEKLY_DAY", "mon")),
        "skip_weekends": env("DIGEST_SKIP_WEEKENDS", "true").lower() != "false",
        "holidays": [d.strip() for d in env("DIGEST_HOLIDAYS", "").split(",") if d.strip()],
    }


def build_engine() -> Engine:
    config = load_yaml(env("CONFIG_PATH", str(HERE / "config.yaml")))

    # Host-specific room id lives in env (MATRIX_ROOM) and overrides config, so
    # the committed config.yaml stays generic and never diverges per server.
    config.setdefault("defaults", {})
    room = env("MATRIX_ROOM")
    if room:
        config["defaults"]["room_id"] = room

    identity = Identity(config.get("users", {}), matrix_domain=config.get("matrix_domain"))

    matrix = MatrixClient(
        env("MATRIX_HOMESERVER", required=True),
        env("MATRIX_TOKEN", required=True),
    )

    # Runtime state/settings (admin panel) live in the state DB and override
    # config + templates. Build the store first so the renderer can read
    # template overrides from it.
    store = Store()
    settings = Settings(store, defaults=_schedule_defaults())
    renderer = Renderer(HERE / "templates", identity=identity, store=store)

    transports = {
        "room": MatrixTransport(matrix, identity=identity),                     # shared room
        "dm": MatrixDirectTransport(matrix, identity=identity, settings=settings),  # personal DM
    }
    # When email is enabled later:
    #   transports["email"] = EmailTransport(host=env("SMTP_HOST"), ...)
    engine = Engine(config, renderer, transports, identity=identity, settings=settings)
    # Stash shared handles so app.py (admin) and cron.py reuse one DB/settings/matrix.
    engine.store = store
    engine.settings = settings
    engine.matrix = matrix
    engine.config = config
    engine.templates_dir = HERE / "templates"
    return engine
