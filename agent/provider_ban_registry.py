"""Persistent provider/model ban registry.

Cross-session, cross-process ban file at ``~/.hermes/provider_bans.json``.
Powers the timeout-based auto-fallback: when a provider/model gives no
response for N minutes, it's banned for 2 hours across all Hermes sessions
(CLI, gateway, subagents, cron).

Ban file structure (JSON)::

    {
      "provider|model": {
        "banned_at": 1748347200.0,
        "banned_until": 1748354400.0,
        "reason": "timeout",
        "session_id": "abc123"
      }
    }

On expiry, the registry makes a lightweight test call to determine if the
model has recovered. If it continues to time out, the ban is refreshed
for another 2 hours. If it responds, the ban is removed.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

BAN_FILE_REL = "provider_bans.json"
DEFAULT_BAN_DURATION = 7200     # 2 hours
STALE_TIMEOUT_THRESHOLD = 180   # 3 minutes (no response → ban)
STALE_WARMING_THRESHOLD = 90    # 90 seconds (log + prepare fallback)

# Thread lock for concurrent read/write safety
_lock = threading.Lock()


# ── Path resolution ──────────────────────────────────────────────────────────

def _ban_file_path() -> Path:
    """Resolve the absolute path to the ban registry file."""
    from hermes_constants import get_hermes_home
    hermes_home = get_hermes_home()
    return hermes_home / BAN_FILE_REL


def _ensure_ban_dir() -> None:
    """Create the parent directory for the ban file if needed."""
    path = _ban_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)


# ── Read / write ─────────────────────────────────────────────────────────────

def _read_bans() -> dict[str, dict[str, Any]]:
    """Read the ban file; return empty dict on any error."""
    path = _ban_file_path()
    if not path.exists():
        return {}
    try:
        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            return {}
        return json.loads(raw)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read provider bans: %s", exc)
        return {}


def _write_bans(bans: dict[str, dict[str, Any]]) -> None:
    """Atomically write the ban file."""
    _ensure_ban_dir()
    path = _ban_file_path()
    tmp = path.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(bans, indent=2), encoding="utf-8")
        tmp.replace(path)
    except OSError as exc:
        logger.error("Failed to write provider bans: %s", exc)


def _ban_key(provider: str, model: str) -> str:
    """Canonical key for the ban registry."""
    return f"{provider}|{model}"


# ── Core API ─────────────────────────────────────────────────────────────────

def is_banned(provider: str, model: str, now: float | None = None) -> bool:
    """Return True if *provider/model* is currently banned (not yet expired).

    Expired entries are cleaned up on read, and a background test call is
    dispatched to re-check the model.  If the test call succeeds, the
    ban is removed before returning.
    """
    if not provider or not model:
        return False
    key = _ban_key(provider, model)
    if now is None:
        now = time.time()

    with _lock:
        bans = _read_bans()
        entry = bans.get(key)
        if entry is None:
            return False

        banned_until = entry.get("banned_until", 0)
        if now < banned_until:
            return True  # still within ban window

        # Ban has expired — remove it and test in background
        del bans[key]
        _write_bans(bans)

    # Launch background test call to see if the model recovered
    _background_test_and_reban(provider, model)

    return False


def ban(provider: str, model: str, duration: float = DEFAULT_BAN_DURATION,
        reason: str = "timeout", session_id: str = "") -> None:
    """Ban *provider/model* for *duration* seconds.

    If already banned, extends the existing ban (uses the max of the
    current expiry and the new one).
    """
    if not provider or not model:
        return
    key = _ban_key(provider, model)
    now = time.time()
    banned_until = now + duration

    with _lock:
        bans = _read_bans()
        existing = bans.get(key)
        if existing:
            existing_until = existing.get("banned_until", 0)
            if existing_until > banned_until:
                # Existing ban lasts longer — don't shorten it
                return
        bans[key] = {
            "banned_at": now,
            "banned_until": banned_until,
            "reason": reason,
            "session_id": session_id or "",
        }
        _write_bans(bans)

    logger.info(
        "Banned %s/%s for %.0fs (reason=%s, until=%s)",
        provider, model, duration, reason,
        time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(banned_until)),
    )


def unban(provider: str, model: str) -> None:
    """Remove a ban for *provider/model* if it exists."""
    key = _ban_key(provider, model)
    with _lock:
        bans = _read_bans()
        if key in bans:
            del bans[key]
            _write_bans(bans)
            logger.info("Unbanned %s/%s — model is responding again", provider, model)


def get_ban_reason(provider: str, model: str) -> str | None:
    """Return the ban reason, or None if not banned."""
    key = _ban_key(provider, model)
    with _lock:
        bans = _read_bans()
        entry = bans.get(key)
        if entry and entry.get("banned_until", 0) > time.time():
            return entry.get("reason")
    return None


def get_banned_until(provider: str, model: str) -> float | None:
    """Return the monotonic expiry timestamp, or None if not banned."""
    key = _ban_key(provider, model)
    with _lock:
        bans = _read_bans()
        entry = bans.get(key)
        if entry and entry.get("banned_until", 0) > time.time():
            return entry["banned_until"]
    return None


def clear_all_bans() -> None:
    """Remove all bans (manual override / debug)."""
    with _lock:
        _write_bans({})
    logger.info("All provider bans cleared")


# ── Background test call ─────────────────────────────────────────────────────

def _background_test_and_reban(provider: str, model: str) -> None:
    """Make a lightweight test call in a daemon thread.

    If the model responds within 30s, unban it.  If it times out,
    refresh the ban for another 2 hours.
    """
    t = threading.Thread(
        target=_do_test_call,
        args=(provider, model),
        daemon=True,
    )
    t.start()


def _do_test_call(provider: str, model: str) -> None:
    """Run a minimal API test call to check if *model* is alive.

    Uses a very short timeout.  If we get any response, the model is
    considered recovered.  If we get a timeout or connection error,
    the ban is refreshed.
    """
    try:
        from hermes_cli.timeouts import get_provider_request_timeout
    except ImportError:
        # Hard to test in a broken env — just bail silently
        logger.debug("Cannot import timeout helpers for ban test call")
        return

    test_timeout = min(get_provider_request_timeout(provider, model) or 30.0, 30.0)

    try:
        # Use a simple head-request style call — ask for a minimal chat completion
        from agent.auxiliary_client import resolve_provider_client
        client, resolved_model = resolve_provider_client(
            provider, model=model, raw_codex=True,
        )
        if client is None:
            logger.warning("Ban test: cannot resolve client for %s/%s", provider, model)
            return

        import httpx

        # Quick ping — tiny messages list, minimal tokens
        resp = client.chat.completions.create(
            model=resolved_model,
            messages=[{"role": "user", "content": "Say 'ok'"}],
            max_tokens=5,
            timeout=httpx.Timeout(connect=10.0, read=test_timeout, write=10.0, pool=10.0),
        )
        if resp and resp.choices and resp.choices[0].message:
            logger.info(
                "Ban expiry test PASSED for %s/%s — unbanning",
                provider, model,
            )
            unban(provider, model)
        else:
            _refresh_ban(provider, model, "empty_response")
    except Exception as exc:
        logger.info(
            "Ban expiry test FAILED for %s/%s (%s) — re-banning for 2h",
            provider, model, exc,
        )
        _refresh_ban(provider, model, f"test_failed:{exc!s}")


def _refresh_ban(provider: str, model: str, reason: str) -> None:
    """Refresh the ban for another full duration."""
    ban(provider, model, duration=DEFAULT_BAN_DURATION, reason=f"re-banned:{reason}")


# ── Stale-timeout warming (90s / 180s) ────────────────────────────────────────

def check_stream_staleness(
    last_chunk_time: float,
    now: float | None = None,
    *,
    warming_logged: list[bool] | None = None,
) -> str | None:
    """Check if a streaming call has gone stale.

    Returns ``None`` when below both thresholds, ``"warming"`` after
    *STALE_WARMING_THRESHOLD* (90s), and ``"banned"`` after
    *STALE_TIMEOUT_THRESHOLD* (180s).

    ``warming_logged`` is a mutable one-shot flag — pass ``[False]`` on
    the first call and this function sets it to ``[True]`` on first
    warming trigger so the caller can log once instead of every poll
    iteration.
    """
    if now is None:
        now = time.time()
    elapsed = now - last_chunk_time

    if elapsed >= STALE_TIMEOUT_THRESHOLD:
        return "banned"
    if elapsed >= STALE_WARMING_THRESHOLD:
        if warming_logged is not None and not warming_logged[0]:
            warming_logged[0] = True
            logger.warning(
                "Stream stale for %.0fs (warming threshold %.0fs). "
                "Preparing fallback.",
                elapsed, STALE_WARMING_THRESHOLD,
            )
        return "warming"
    return None
