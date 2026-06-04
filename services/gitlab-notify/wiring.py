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

HERE = Path(__file__).parent


def build_engine() -> Engine:
    config = load_yaml(env("CONFIG_PATH", str(HERE / "config.yaml")))

    # Host-specific room id lives in env (MATRIX_ROOM) and overrides config, so
    # the committed config.yaml stays generic and never diverges per server.
    config.setdefault("defaults", {})
    room = env("MATRIX_ROOM")
    if room:
        config["defaults"]["room_id"] = room

    identity = Identity(config.get("users", {}), matrix_domain=config.get("matrix_domain"))
    renderer = Renderer(HERE / "templates", identity=identity)

    matrix = MatrixClient(
        env("MATRIX_HOMESERVER", required=True),
        env("MATRIX_TOKEN", required=True),
    )
    transports = {
        "room": MatrixTransport(matrix, identity=identity),        # shared room
        "dm": MatrixDirectTransport(matrix, identity=identity),    # personal DM (off in config by default)
    }
    # When email is enabled later:
    #   transports["email"] = EmailTransport(host=env("SMTP_HOST"), ...)
    return Engine(config, renderer, transports, identity=identity)
