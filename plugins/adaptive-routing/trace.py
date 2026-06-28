"""Bounded in-memory route trace for the adaptive-routing plugin.

Records the last N route decisions for diagnostic commands.  Disabled
by default — the user opts in via ``plugins.adaptive_routing.trace.enabled``.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Deque, Optional

from .policy import RouteDecision


class RouteTrace:
    """Bounded, thread-safe ring buffer of recent route decisions."""

    def __init__(self, max_history: int = 10) -> None:
        self._max = max(0, int(max_history))
        self._buffer: Deque[dict] = deque(maxlen=self._max)
        self._lock = threading.Lock()

    def record(self, decision: RouteDecision, *, session_key: str = "") -> None:
        if self._max <= 0:
            return
        entry = {
            "ts": time.time(),
            "session_key": session_key,
            "provider": decision.provider,
            "model": decision.model,
            "tier": decision.tier,
            "stack": decision.stack_name,
            "effort": decision.reasoning_effort,
            "label": decision.route_label,
            "score": decision.score_breakdown,
            "fallback_reason": decision.fallback_reason,
            "route_source": decision.route_source,
        }
        with self._lock:
            self._buffer.append(entry)

    def snapshot(self) -> list[dict]:
        with self._lock:
            return list(self._buffer)

    def clear(self) -> None:
        with self._lock:
            self._buffer.clear()
