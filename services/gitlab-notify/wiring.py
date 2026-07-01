"""Shared wiring for the webhook app and the cron job.

Builds the notification engine from config.yaml + env so both entrypoints
(app.py, cron.py) deliver through exactly the same rules/templates/transports.
"""
from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

from botkit.config import env, load_yaml
from botkit.identity import Identity
from botkit.matrix import MatrixClient
from botkit.notify.engine import Engine
from botkit.notify.guard import SendGuard
from botkit.notify.render import Renderer
from botkit.notify.transports.matrix import MatrixTransport
from botkit.notify.transports.matrix_direct import MatrixDirectTransport
from botkit.store import Store

from alerts import Alerter
from context import AppContext
from settings import Settings, migrate_split_digests
from sources import Sources, seed_from_env

HERE = Path(__file__).parent
log = logging.getLogger("gitlab-notify.wiring")


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
    # Label-display whitelist: templates call display_labels(labels) to honour
    # the admin "which 🏷 to show" setting. Registered here (not in botkit) so the
    # generic renderer stays settings-agnostic; reads the DB live on each render.
    renderer.env.globals["display_labels"] = settings.display_labels
    # Comment truncation for watched-issue note notifications (chars + lines, live
    # admin config). Same rationale as display_labels — kept out of botkit.
    renderer.env.globals["clip_comment"] = settings.clip_comment
    # Plain-text display names (no @-mention pill) for the issue card, where the
    # assignee is shown as info, not pinged. Autoescape handles the returned str.
    renderer.env.globals["names"] = lambda logins: ", ".join(
        identity.display_name(x) for x in (logins or []) if x)

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

    # Phase 2a: one-time, idempotent split of the old anchor-dual digest/team
    # passes into separate full + delta passes (carries the live schedule over and
    # seeds run-history so the scheduler treats them as established). Runs after
    # sources are seeded (so it can key run-history per source) and before any
    # scheduler tick. Marker-guarded — a no-op on every boot after the first.
    migrate_split_digests(store)

    # Operator alerting: DM engineers when a scheduled pass throws (see alerts.py).
    alerter = Alerter(matrix, identity, settings, store)

    # SAFETY: send guard (rate-limit + circuit breaker). Built once at boot from
    # the current guard config — threshold edits in the admin panel take effect
    # on the next process restart, but a RESET works live (admin calls
    # ctx.guard.reset()). The tripped flag is persisted via settings.get/set_breaker
    # so a crash-loop can't resume spamming. When it trips it both DMs the
    # engineers (reusing the Alerter, throttled per day) and logs an error row.
    def _on_trip(reason: str) -> None:
        today = dt.date.today().isoformat()
        try:
            alerter.alert("⛔ Защита от спама сработала — бот ОСТАНОВЛЕН",
                          reason + " Сбросьте предохранитель в админке.",
                          key="breaker", day=today)
        except Exception:                       # noqa: BLE001 — alert is best-effort
            log.exception("breaker alert failed")
        try:
            store.log_event("breaker", "trip", "", "error", reason[:500])
        except Exception:                       # noqa: BLE001
            log.exception("breaker log failed")

    gconf = settings.get_guard()
    guard = SendGuard(
        enabled=gconf["enabled"],
        window_s=gconf["window_s"],
        max_global=gconf["max_global"],
        target_window_s=gconf["target_window_s"],
        max_per_target=gconf["max_per_target"],
        max_duplicate=gconf["max_duplicate"],
        auto_reset_s=gconf["auto_reset_s"],
        get_tripped=settings.get_breaker,
        set_tripped=settings.set_breaker,
        on_trip=_on_trip,
    )

    engine = Engine(config, renderer, transports, identity=identity,
                    settings=settings, guard=guard)
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
        guard=guard,
    )
