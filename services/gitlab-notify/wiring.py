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

from alerts import Alerter
from context import AppContext
from settings import Settings
from sources import Sources, seed_from_env

HERE = Path(__file__).parent


def build_context() -> AppContext:
    config = load_yaml(env("CONFIG_PATH", str(HERE / "config.yaml")))

    # Rooms are per-source now (admin-managed); config.defaults.room_id is only a
    # last-ditch fallback. MATRIX_ROOM is read once below to seed the first source.
    config.setdefault("defaults", {})

    identity = Identity(config.get("users", {}), matrix_domain=config.get("matrix_domain"))

    # Runtime state/settings (admin panel) live in the state DB and override
    # config + templates + connection secrets (env is the fallback default).
    store = Store()
    settings = Settings(store)   # schedule defaults are built-in; admin panel overrides
    settings.seed_conn()         # one-time: env -> DB; afterwards admin panel is authoritative
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
    seed_from_env(sources, group_id=env("GITLAB_GROUP_ID"), token=env("GITLAB_TOKEN"),
                  room=env("MATRIX_ROOM") or config["defaults"].get("room_id"))

    engine = Engine(config, renderer, transports, identity=identity, settings=settings)
    # Operator alerting: DM engineers when a scheduled pass throws (see alerts.py).
    alerter = Alerter(matrix, identity, settings, store)
    # Explicit container: app.py (admin) and cron.py reuse one DB/settings/matrix
    # via ctx instead of monkey-patched engine attributes (see context.py).
    return AppContext(
        engine=engine,
        store=store,
        settings=settings,
        sources=sources,
        matrix=matrix,
        identity=identity,
        renderer=renderer,
        config=config,
        templates_dir=HERE / "templates",
        alerter=alerter,
    )
