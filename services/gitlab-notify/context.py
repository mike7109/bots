"""Explicit service container for the gitlab-notify app.

The botkit `Engine` is a pure library object (rules + transports + renderer). The
service, though, also needs a handful of shared, process-wide handles — the
state DB, runtime settings, the multi-group source registry, the Matrix client,
operator alerting, etc. These used to be monkey-patched onto the Engine as
dynamic attributes, which turned the Engine into a god-object and blurred the
botkit seam.

`AppContext` holds those handles explicitly. `wiring.build_context()` constructs
it; every service module (app/admin/cron/scheduler) takes a `ctx` and reaches
through it (`ctx.store`, `ctx.settings`, …) — the Engine stays clean and is used
only as `ctx.engine` for `handle()`.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class AppContext:
    """Shared, process-wide handles for the webhook app and cron/scheduler.

    Fields are typed loosely on purpose — this is a plumbing container, not a
    place to fight imports.
    """
    engine: object        # botkit.notify.engine.Engine — pure rules/transports
    store: object         # botkit.store.Store — state DB (dedup + settings)
    settings: object      # settings.Settings — runtime settings / kill switch
    sources: object       # sources.Sources — multi-group source registry
    matrix: object        # botkit.matrix.MatrixClient — live Matrix client
    identity: object      # botkit.identity.Identity — login <-> mxid mapping
    renderer: object      # botkit.notify.render.Renderer
    config: dict          # parsed config.yaml
    templates_dir: object # pathlib.Path to templates/
    alerter: object       # alerts.Alerter — operator DM on pass failure
    guard: object = None  # botkit.notify.guard.SendGuard — rate-limit + breaker
