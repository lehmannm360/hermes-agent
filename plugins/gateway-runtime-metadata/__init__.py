"""Gateway runtime-metadata plugin.

Owns runtime footer presentation and response-reference operator surfaces.
Provides a CLI for response-ref lookup and footer diagnostics.

The plugin registers a ``format_gateway_runtime_footer`` hook callback that
plugins can use to customize footer output.  When the hook is not loaded, the
core default footer builder in ``gateway.runtime_footer`` runs as normal.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _format_gateway_runtime_footer_hook(**kwargs: Any) -> Optional[str]:
    """Default hook callback — returns None to defer to core builder.

    This callback is a placeholder that demonstrates the hook contract.
    When this plugin is loaded, it fires alongside any other registered
    callbacks for ``format_gateway_runtime_footer``.  Returning None
    defers to the core ``build_footer_line`` in ``gateway/runtime_footer.py``.

    To customize: replace this function with one that returns a footer
    string, or register additional callbacks via separate plugins.
    """
    return None  # defer to core default


def _on_final_response_persisted_hook(**kwargs: Any) -> None:
    """Notification hook — fires after response-ref is persisted.

    Logs the ref-id for observability.  Return values are ignored.
    """
    ref_id = kwargs.get("ref_id", "")
    session_id = kwargs.get("session_id", "")
    if ref_id:
        logger.debug(
            "Response ref persisted: ref=%s session=%s",
            ref_id,
            session_id,
        )


def register(ctx) -> None:
    """Register the gateway-runtime-metadata plugin surfaces."""
    # Register hook callbacks
    ctx.register_hook(
        "format_gateway_runtime_footer",
        _format_gateway_runtime_footer_hook,
    )
    ctx.register_hook(
        "on_final_response_persisted",
        _on_final_response_persisted_hook,
    )

    # CLI for response-ref lookup
    def _ref_setup(parser):
        parser.add_argument("ref_id", help="Response reference ID to look up")
        parser.add_argument("--session-id", help="Optional session ID hint")

    def _ref_handler(args):
        try:
            from hermes_constants import get_hermes_home
            from hermes_state import SessionDB

            db_path = get_hermes_home() / "sessions.db"
            if not db_path.exists():
                return "No session database found."
            db = SessionDB(str(db_path))
            row = db.resolve_response_ref(args.ref_id)
            if not row:
                return f"No mapping found for ref: {args.ref_id}"
            return (
                f"Ref: {args.ref_id}\n"
                f"Session: {row.get('session_id', 'unknown')}\n"
                f"Message ID: {row.get('message_id', 'unknown')}"
            )
        except Exception as exc:
            return f"Lookup failed: {exc}"

    ctx.register_cli_command(
        name="response-ref",
        help="Look up a response reference ID",
        setup_fn=_ref_setup,
        handler_fn=_ref_handler,
        description="Map response-ref IDs to session/message IDs",
    )
