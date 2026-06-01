from __future__ import annotations

from .usage import *  # noqa: F401,F403


def register(ctx) -> None:
    """Register the account-usage plugin surfaces."""
    from .usage import register_plugin

    register_plugin(ctx)
