"""Manual/auto session-routing lock helpers for the adaptive-routing plugin.

Provides a thin lock primitive that mirrors the gateway's
``_session_model_overrides`` dict.  The plugin stores lock state in
``SessionState``-like maps so it can survive a process restart by
optionally persisting to the per-profile config dir, but the default
in-memory mode is what the gateway uses.

The gateway owns the actual storage (it shares ``_session_model_lock``
on ``GatewayRunner``).  This module is a pure helper — no I/O, no
network.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional


@dataclass(frozen=True)
class ManualLock:
    """An explicit user-selected model lock for a session."""

    session_key: str
    model: str
    provider: str
    source: str = "user"  # user | picker | one_shot

    def as_dict(self) -> Dict[str, str]:
        return {
            "model": self.model,
            "provider": self.provider,
            "source": self.source,
        }


def is_locked(locks: Optional[Dict[str, Dict[str, str]]], session_key: str) -> bool:
    return bool(locks) and session_key in locks


def get_lock(locks: Optional[Dict[str, Dict[str, str]]], session_key: str) -> Optional[Dict[str, str]]:
    if not locks:
        return None
    return locks.get(session_key)


def set_lock(
    locks: Dict[str, Dict[str, str]],
    session_key: str,
    *,
    model: str,
    provider: str,
    source: str = "user",
) -> ManualLock:
    lock = ManualLock(session_key=session_key, model=model, provider=provider, source=source)
    locks[session_key] = lock.as_dict()
    return lock


def clear_lock(locks: Dict[str, Dict[str, str]], session_key: str) -> bool:
    """Remove the lock for *session_key*.  Returns True if a lock was cleared."""
    return locks.pop(session_key, None) is not None


def clear_with_targets(
    locks: Dict[str, Dict[str, str]],
    session_key: str,
    targets: Iterable[str],
) -> bool:
    """Clear the lock if *session_key* matches one of *targets*.

    The plan names the default triggers as ``route_auto``, ``/new``,
    ``/reset``.  This function is a strict equality check; callers
    decide which events to translate into which target name.
    """
    targets_set = set(targets)
    if session_key in targets_set:
        return clear_lock(locks, session_key)
    return False
