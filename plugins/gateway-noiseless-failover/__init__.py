"""Gateway noiseless failover plugin.

Owns user-visible status policy for fallback recovery and stream warming
after the ``transform_status_event`` hook exists.

This plugin is currently **policy-only**: it defines the suppression/
visibility policy tables but does not fire a live hook because
``transform_status_event`` is declared but not yet fired in the
codebase.  When the hook becomes live, this plugin will register a
callback that applies the policy.

Policy invariants:
- Successful automatic fallback does NOT suppress the final result.
- Terminal failures, auth failures, billing failures, missing fallback
  provider, and content-policy blocks remain ALWAYS visible.
- Stream-warming warnings appear at the configured threshold.
- Plugin-disabled mode returns to upstream/default status visibility.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Policy tables
# ---------------------------------------------------------------------------

# Status event kinds that should be suppressed (not shown to users) when
# they represent transient recovery noise rather than user-actionable errors.
_SUPPRESS_KINDS = frozenset({
    "fallback_attempt",
    "provider_retry",
    "stream_warming",
    "connection_recovering",
})

# Status event kinds that MUST remain visible regardless of policy.
# These are terminal or user-actionable.
_FORCE_VISIBLE_KINDS = frozenset({
    "auth_failure",
    "billing_exhausted",
    "quota_exhausted",
    "content_policy_blocked",
    "all_providers_failed",
    "missing_fallback",
    "terminal_error",
    "fatal_error",
})

# Status event kinds that should be logged but not shown to users at
# all (even in verbose mode).
_SILENT_KINDS = frozenset({
    "stream_heartbeat",
    "provider_health_check",
})


def should_suppress_status(
    kind: str,
    *,
    is_terminal: bool = False,
    attempt: int = 1,
) -> Dict[str, Any]:
    """Evaluate the noiseless-failover policy for a status event.

    Returns a dict with the policy decision::

        {"suppress": bool, "force_visible": bool, "silent": bool, "reason": str}

    This function is pure and side-effect-free.  It does not fire any
    hooks or modify any state.  When the ``transform_status_event``
    hook becomes live, the plugin callback will delegate to this.
    """
    # Terminal events are always visible.
    if is_terminal:
        return {
            "suppress": False,
            "force_visible": True,
            "silent": False,
            "reason": "terminal_event",
        }

    # Force-visible kinds always surface.
    if kind in _FORCE_VISIBLE_KINDS:
        return {
            "suppress": False,
            "force_visible": True,
            "silent": False,
            "reason": f"force_visible:{kind}",
        }

    # Silent kinds — logged but not shown.
    if kind in _SILENT_KINDS:
        return {
            "suppress": True,
            "force_visible": False,
            "silent": True,
            "reason": f"silent:{kind}",
        }

    # Suppress transient recovery noise after the first attempt.
    if kind in _SUPPRESS_KINDS and attempt > 1:
        return {
            "suppress": True,
            "force_visible": False,
            "silent": False,
            "reason": f"suppress_after_attempt:{kind}",
        }

    # First attempt of a suppressible kind — let it through so users
    # see the initial recovery notice.
    if kind in _SUPPRESS_KINDS:
        return {
            "suppress": False,
            "force_visible": False,
            "silent": False,
            "reason": f"first_attempt:{kind}",
        }

    # Default: don't suppress unknown kinds.
    return {
        "suppress": False,
        "force_visible": False,
        "silent": False,
        "reason": "default_pass",
    }


def register(ctx) -> None:
    """Register the gateway-noiseless-failover plugin.

    Currently registers only the CLI diagnostics.  The
    ``transform_status_event`` hook callback will be registered when
    the hook becomes live in the codebase.
    """
    # CLI for policy diagnostics
    def _policy_setup(parser):
        parser.add_argument(
            "kind",
            help="Status event kind to evaluate",
        )
        parser.add_argument("--terminal", action="store_true", help="Mark as terminal")
        parser.add_argument("--attempt", type=int, default=1, help="Attempt number")

    def _policy_handler(args):
        decision = should_suppress_status(
            args.kind,
            is_terminal=args.terminal,
            attempt=args.attempt,
        )
        action = (
            "SILENT" if decision["silent"]
            else "SUPPRESS" if decision["suppress"]
            else "FORCE_VISIBLE" if decision["force_visible"]
            else "PASS"
        )
        return (
            f"Kind: {args.kind}\n"
            f"Action: {action}\n"
            f"Reason: {decision['reason']}\n"
            f"Terminal: {decision['force_visible']}"
        )

    ctx.register_cli_command(
        name="noiseless-failover",
        help="Evaluate status event visibility policy",
        setup_fn=_policy_setup,
        handler_fn=_policy_handler,
        description="Quiet fallback and stream warming policy diagnostics",
    )
