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
from sources import Sources, seed_from_env

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

    # Runtime state/settings (admin panel) live in the state DB and override
    # config + templates + connection secrets (env is the fallback default).
    store = Store()
    settings = Settings(store, defaults=_schedule_defaults())
    settings.seed_conn()       # one-time: env -> DB; afterwards admin panel is authoritative
    renderer = Renderer(HERE / "templates", identity=identity, store=store)

    # Matrix homeserver/token come from settings (DB; seeded from env once) so
    # they're admin-managed; not required at boot — the panel configures them.
    matrix = MatrixClient(settings.conn_value("matrix_homeserver"),
                          settings.conn_value("matrix_token"))

    transports = {
        "room": MatrixTransport(matrix, identity=identity),                     # shared room
        "dm": MatrixDirectTransport(matrix, identity=identity, settings=settings),  # personal DM
    }
    # When email is enabled later:
    #   transports["email"] = EmailTransport(host=env("SMTP_HOST"), ...)
    # Multi-group sources (admin-managed). Seed the first one from env so an
    # existing single-group deploy keeps working with no config.
    sources = Sources(store, settings)
    seed_from_env(sources, group_id=env("GITLAB_GROUP_ID"),
                  token=env("GITLAB_TOKEN"), room=config["defaults"].get("room_id"))

    engine = Engine(config, renderer, transports, identity=identity, settings=settings)
    # Stash shared handles so app.py (admin) and cron.py reuse one DB/settings/matrix.
    engine.store = store
    engine.settings = settings
    engine.sources = sources
    engine.matrix = matrix
    engine.config = config
    engine.templates_dir = HERE / "templates"
    return engine
