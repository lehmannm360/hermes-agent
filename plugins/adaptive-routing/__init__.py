"""Adaptive routing plugin.

Owns task-profile classification, MiMo-first route selection, Codex
quota-aware fallback, DeepSeek last-resort fallback, and route labels
via the ``resolve_turn_route`` hook.

When the plugin is disabled, the core adaptive routing in
``gateway/run.py::_resolve_turn_agent_config`` runs using
``agent/reasoning_policy.py`` directly.

Cache-safety contract: the hook receives only explicit turn inputs
(user message, primary provider/model, session key, policy dict).
It must NOT mutate or return messages, history, tools, toolsets,
system prompt, or memory.  The caller ignores dangerous returned keys.

Session override precedence: explicit per-session reasoning overrides
(from /reasoning) always outrank this hook.  The gateway fire site
gates the hook behind ``force_reasoning_config=False``.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


def _resolve_turn_route_hook(**kwargs: Any) -> Optional[Dict[str, Any]]:
    """Route decision hook callback.

    Returns a dict with routing overrides, or None (defer to core logic).

    The core ``_resolve_turn_agent_config`` will call ``decide_turn_route``
    from ``agent.reasoning_policy`` regardless of this hook's return value.
    The hook's overrides are applied first; the core decision may then
    override them.  This means the hook acts as a pre-filter/preference
    rather than a hard override when both are active.

    When the policy is not enabled (``policy.get("enabled") is False``),
    the hook still fires but the core won't call ``decide_turn_route``.
    """
    policy = kwargs.get("policy", {})
    if not policy or not policy.get("enabled"):
        return None  # Nothing to do when routing policy is disabled.

    user_message = kwargs.get("user_message", "")
    primary_provider = kwargs.get("primary_provider", "")
    primary_model = kwargs.get("primary_model", "")

    try:
        from agent.reasoning_policy import (
            classify_task,
            decide_turn_route,
            is_codex_provider,
        )

        # Use the core policy to classify and decide, then return the
        # decision as hook overrides.  This ensures the hook's behavior
        # is consistent with the core policy module.
        decision = decide_turn_route(
            user_message,
            primary_provider=primary_provider,
            primary_model=primary_model,
            policy=policy,
        )
        return {
            "provider": decision.provider,
            "model": decision.model,
            "reasoning_effort": decision.reasoning_effort,
            "route_label": decision.route_label,
            "runtime_provider": decision.runtime_provider or decision.provider,
        }
    except Exception as exc:
        logger.debug("adaptive-routing hook error: %s", exc)
        return None  # defer to core


def register(ctx) -> None:
    """Register the adaptive-routing plugin surfaces."""
    ctx.register_hook(
        "resolve_turn_route",
        _resolve_turn_route_hook,
    )

    # CLI for routing diagnostics
    def _route_setup(parser):
        parser.add_argument("message", help="User message to classify")
        parser.add_argument("--provider", default="", help="Primary provider")
        parser.add_argument("--model", default="", help="Primary model")

    def _route_handler(args):
        try:
            from agent.reasoning_policy import (
                DEFAULT_REASONING_POLICY,
                classify_task,
                decide_turn_route,
                format_route_footer,
            )
            policy = DEFAULT_REASONING_POLICY
            profile = classify_task(args.message, policy)
            decision = decide_turn_route(
                args.message,
                primary_provider=args.provider,
                primary_model=args.model,
                policy=policy,
            )
            return (
                f"Difficulty: {profile.difficulty}\n"
                f"Score: {profile.score}\n"
                f"Provider: {decision.provider}\n"
                f"Model: {decision.model}\n"
                f"Effort: {decision.reasoning_effort}\n"
                f"Label: {decision.route_label}\n"
                f"Footer: {format_route_footer(decision)}"
            )
        except Exception as exc:
            return f"Classification failed: {exc}"

    ctx.register_cli_command(
        name="adaptive-routing",
        help="Classify and route a user message",
        setup_fn=_route_setup,
        handler_fn=_route_handler,
        description="Task classification and route diagnostics",
    )
