"""Generic quota/account-snapshot service seam.

Provides a stable interface for fetching and rendering account usage
quotas so that hot gateway code never imports plugin modules directly.

The ``account-usage`` plugin registers its fetcher and renderer via
:func:`register_quota_fetcher` / :func:`register_quota_renderer` during
plugin discovery.  Gateway code calls :func:`fetch_quota_snapshot` and
:func:`render_quota_lines` instead of importing
``plugins.account_usage.usage``.

When the account-usage plugin is disabled or not loaded, every public
function degrades gracefully (returns ``None`` / empty list).
"""

from __future__ import annotations

import logging
from typing import Any, Callable, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_fetcher: Optional[Callable[..., Any]] = None
_renderer: Optional[Callable[..., List[str]]] = None


def register_quota_fetcher(fn: Callable[..., Any]) -> None:
    """Register the callable that fetches a quota snapshot for a provider.

    The callable signature must be compatible with::

        fn(provider: str, **kwargs) -> Optional[AccountUsageSnapshot]

    Only one fetcher may be active; last registration wins.
    """
    global _fetcher
    _fetcher = fn


def register_quota_renderer(fn: Callable[..., List[str]]) -> None:
    """Register the callable that renders snapshot lines for display.

    The callable signature must be compatible with::

        fn(snapshot, *, markdown: bool = False) -> list[str]
    """
    global _renderer
    _renderer = fn


# ---------------------------------------------------------------------------
# Public accessors
# ---------------------------------------------------------------------------

def fetch_quota_snapshot(provider: str, **kwargs: Any) -> Any:
    """Fetch a quota snapshot for *provider*.

    Returns the provider-specific snapshot object, or ``None`` when the
    fetcher is unavailable or raises.  Never propagates exceptions.
    """
    if _fetcher is None:
        return None
    try:
        return _fetcher(provider, **kwargs)
    except Exception as exc:
        logger.debug("Quota snapshot fetch failed for %s: %s", provider, exc)
        return None


def render_quota_lines(snapshot: Any, *, markdown: bool = False) -> List[str]:
    """Render *snapshot* into display lines.

    Returns an empty list when no renderer is registered or the snapshot
    is falsy.
    """
    if _renderer is None or not snapshot:
        return []
    try:
        return _renderer(snapshot, markdown=markdown)
    except Exception as exc:
        logger.debug("Quota render failed: %s", exc)
        return []
