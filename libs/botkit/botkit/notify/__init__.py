"""Config-driven notification engine.

Flow:  Event  ->  rules (config.yaml)  ->  template (Jinja2)  ->  transports.

A bot normalizes its incoming payload into an `Event`, hands it to `Engine`,
and the engine decides — from config, not code — which template to render and
which channels (Matrix now, email later) to deliver it to.
"""
from botkit.notify.engine import Engine
from botkit.notify.event import Event
from botkit.notify.render import Renderer

__all__ = ["Engine", "Event", "Renderer"]
