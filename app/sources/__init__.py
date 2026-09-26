"""Pluggable lead sources. Register a new adapter in registry.build_default_registry."""

from app.sources.registry import default_registry

__all__ = ["default_registry"]
