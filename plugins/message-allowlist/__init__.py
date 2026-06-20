"""Message allowlist plugin.

Owns cross-platform member registry parsing, identity matching, diagnostics,
and authorization decisions via the ``pre_gateway_authorize_message`` hook.

When this plugin is loaded, it registers an authorization hook callback that
checks incoming messages against the ``security.message_allowlist`` config.
When the plugin is disabled, the core ``_is_user_authorized`` method in
``gateway/run.py`` handles authorization using env-var allowlists and pairing.

Fail-closed contract: the gateway fire site denies when the hook has
registered callbacks and none explicitly return ``{"allow": True}``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _pre_gateway_authorize_message_hook(**kwargs: Any) -> Optional[Dict[str, Any]]:
    """Authorization hook callback.

    Checks the incoming message source against the cross-platform message
    allowlist configuration.  Returns ``{"allow": True}`` if the source
    matches a configured member, or ``{"deny": True, "reason": "..."}``
    otherwise.

    When the allowlist is not configured or not enabled, returns None
    (defer to next callback or default).
    """
    source = kwargs.get("source")
    if source is None:
        return None

    try:
        from gateway.message_allowlist import (
            matching_message_allowlist_member,
            message_allowlist_enabled,
        )

        # If the allowlist block is absent/disabled, do not engage the
        # fail-closed plugin gate.  The gateway has already run its core
        # _is_user_authorized check before firing this hook, so an explicit
        # allow here preserves default behavior when the optional plugin is
        # enabled without security.message_allowlist enabled.
        if not message_allowlist_enabled():
            return {"allow": True, "reason": "allowlist_not_enabled"}

        member = matching_message_allowlist_member(source)
        if member is not None:
            return {"allow": True, "reason": f"member:{member.get('member_id', '?')}"}
        # Deny — source doesn't match any configured member.
        # The fail-closed fire site will handle this.
        return {"deny": True, "reason": "not_in_allowlist"}
    except Exception as exc:
        logger.warning("message-allowlist auth hook error: %s", exc)
        # On error, return deny to fail closed.
        return {"deny": True, "reason": f"hook_error:{exc}"}


def register(ctx) -> None:
    """Register the message-allowlist plugin surfaces."""
    # Register the authorization hook
    ctx.register_hook(
        "pre_gateway_authorize_message",
        _pre_gateway_authorize_message_hook,
    )

    # CLI for allowlist diagnostics
    def _check_setup(parser):
        parser.add_argument("--platform", help="Platform to check (e.g. telegram)")
        parser.add_argument("--user-id", help="User ID to check")
        parser.add_argument("--user-name", help="User name to check")

    def _check_handler(args):
        try:
            from gateway.message_allowlist import (
                message_allowlist_configured,
                matching_message_allowlist_member,
            )
            if not message_allowlist_configured():
                return "Message allowlist is not configured or not enabled."
            # Build a synthetic source for the check
            from types import SimpleNamespace
            platform_enum = None
            if args.platform:
                try:
                    from gateway.platforms.base import Platform
                    platform_enum = Platform(args.platform)
                except Exception:
                    return f"Unknown platform: {args.platform}"
            synthetic = SimpleNamespace(
                user_id=args.user_id or "",
                user_name=args.user_name or "",
                user_id_alt=None,
                chat_id=None,
                chat_id_alt=None,
                platform=platform_enum,
                chat_type="dm",
            )
            member = matching_message_allowlist_member(synthetic)
            if member:
                return (
                    f"✓ Authorized\n"
                    f"Member: {member.get('member_id', '?')}\n"
                    f"Display: {member.get('display_name', '?')}\n"
                    f"Role: {member.get('role', '?')}"
                )
            return "✗ Not authorized — no matching member found."
        except Exception as exc:
            return f"Check failed: {exc}"

    ctx.register_cli_command(
        name="message-allowlist",
        help="Check message allowlist authorization",
        setup_fn=_check_setup,
        handler_fn=_check_handler,
        description="Cross-platform member registry diagnostics",
    )
