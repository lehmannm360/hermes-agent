"""Adaptive routing plugin.

Owns task-profile classification, balanced scoring, and stack selection
for the Opencode Go / OpenAI Codex / DeepSeek PAYG model tiers via the
``resolve_turn_route`` hook.

When the plugin is disabled, core adaptive routing in
``gateway/run.py::_resolve_turn_agent_config`` runs using
``agent/reasoning_policy.py`` directly.  When the plugin is enabled
and returns a *complete* decision (provider, model, reasoning_effort,
route_label), the gateway applies the plugin's decision directly and
skips core ``decide_turn_route()``.  When the plugin returns ``None``
or an advisory result, the gateway falls back to core routing.

Manual/auto routing:
- Manual model selection (``/model <name>``) sets a per-session lock.
- While locked, adaptive routing must not change provider/model.
- The lock is cleared by ``/model auto``, ``/new``, ``/reset`` — and
  by the gateway's existing finalization paths.

Cache-safety contract: the hook receives only explicit turn inputs
(user message, primary provider/model, session key, policy dict,
quota snapshots).  It must NOT mutate or return messages, history,
tools, toolsets, system prompt, or memory.  The caller filters out
dangerous returned keys regardless.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from .manual_lock import (
    ManualLock,
    clear_lock,
    is_locked,
    set_lock,
)
from .policy import (
    DEFAULT_STACKS,
    QuotaState,
    RouteDecision,
    build_quota_states,
    build_stacks,
    classify_tier,
    extract_features,
    load_plugin_config,
    reasoning_effort_for,
    select_route,
)
from .trace import RouteTrace

logger = logging.getLogger(__name__)


# Process-global trace buffer (bounded, opt-in).  Cleared on import;
# never persisted to disk.
_TRACE = RouteTrace(max_history=10)


# ---------------------------------------------------------------------------
# Quota lookup via the gateway's generic seam
# ---------------------------------------------------------------------------


def _fetch_quota_state(provider: str) -> QuotaState:
    """Resolve a provider's QuotaState via the gateway quota service seam.

    Returns ``QuotaState.unknown(provider)`` on any failure.  Never
    raises.  PAYG providers and providers with no supported quota
    source today (``unknown`` type — e.g. Opencode Go / Zen) are
    detected via the plugin's default quota policy table and resolve
    to a no-penalty PAYG state.
    """
    from .policy import DEFAULT_QUOTA_POLICY

    default_policy = DEFAULT_QUOTA_POLICY.get(provider, {})
    if default_policy.get("type") in {"payg", "unknown"}:
        return QuotaState.payg(provider)
    try:
        from gateway.quota_service import fetch_quota_snapshot
    except Exception:
        return QuotaState.unknown(provider)
    try:
        snapshot = fetch_quota_snapshot(provider)
    except Exception as exc:
        logger.debug("adaptive-routing quota fetch failed for %s: %s", provider, exc)
        return QuotaState.unknown(provider)
    if snapshot is None:
        return QuotaState.unknown(provider)
    windows = getattr(snapshot, "windows", None)
    if not windows:
        return QuotaState.unknown(provider)
    try:
        from agent.reasoning_policy import CodexQuotaState
        derived = CodexQuotaState.from_usage_snapshot(snapshot)
    except Exception:
        return QuotaState.unknown(provider)
    if derived.unavailable or derived.percent_remaining is None:
        return QuotaState.unknown(provider)
    return QuotaState(
        provider=provider,
        percent_remaining=derived.percent_remaining,
        reset_at=derived.reset_at,
        unavailable=False,
        is_payg=False,
    )


def _quota_states_for(providers: List[str]) -> Dict[str, QuotaState]:
    out: Dict[str, QuotaState] = {}
    for provider in providers:
        out[provider] = _fetch_quota_state(provider)
    return out


# ---------------------------------------------------------------------------
# Hook callback
# ---------------------------------------------------------------------------


_DANGEROUS_KEYS = frozenset({
    "messages", "history", "tools", "toolsets", "system", "memory",
})


def _safe_overrides(payload: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    return {k: v for k, v in payload.items() if k not in _DANGEROUS_KEYS}


def _resolve_turn_route_hook(**kwargs: Any) -> Optional[Dict[str, Any]]:
    """Resolve-turn-route hook callback.

    Contract: returns one of:
      - ``None``: defer entirely to core routing.
      - ``{"final_decision": True, ...}``: a complete plugin decision
        the gateway must apply directly and skip core routing.
      - ``{"final_decision": False, ...}``: advisory overrides; the
        gateway should apply the safe keys (route_label, reasoning
        effort) but still let core ``decide_turn_route()`` choose the
        provider/model.
    """
    policy = kwargs.get("policy", {})
    if not isinstance(policy, dict) or not policy.get("enabled"):
        return None

    session_key = str(kwargs.get("session_key") or "")
    # Honor a manual model lock — if one is set, return a final_decision
    # that pins to the locked provider/model and lets the gateway
    # skip core adaptive routing.
    locks_map = _session_locks_ref()
    if session_key and is_locked(locks_map, session_key):
        lock = locks_map[session_key]
        return {
            "provider": lock.get("provider"),
            "model": lock.get("model"),
            "reasoning_effort": str(lock.get("reasoning_effort") or "medium"),
            "route_label": "manual",
            "route_source": "manual",
            "stack_name": "manual",
            "final_decision": True,
        }

    user_message = kwargs.get("user_message", "")
    primary_provider = str(kwargs.get("primary_provider") or "")
    primary_model = str(kwargs.get("primary_model") or "")

    try:
        plugin_cfg = load_plugin_config({"plugins": {"adaptive_routing": policy}} if "stacks" in policy else {"plugins": {"adaptive_routing": policy}})
        stacks = build_stacks(plugin_cfg.get("stacks"))
        scoring = plugin_cfg.get("scoring") or {}
        weights = {k: float(v) for k, v in scoring.items() if isinstance(v, (int, float))}

        # Resolve quota states for the providers that appear in any of our stacks.
        providers = sorted({cand.provider for stack in stacks.values() for cand in stack.candidates.values()})
        quota_states = _quota_states_for(providers)

        # If a session is bound to a provider that has been exhausted,
        # exclude it so the next stack in the order takes over.
        excluded: set[tuple[str, str]] = set()
        if primary_provider and primary_model:
            for provider, state in quota_states.items():
                if state.percent_remaining is not None and state.percent_remaining <= 0:
                    excluded.add((provider, primary_model))

        decision = select_route(
            user_message,
            stacks=stacks,
            quota_states=quota_states,
            scoring_weights=weights,
            excluded=excluded,
        )
    except Exception as exc:
        logger.debug("adaptive-routing policy engine error: %s", exc)
        return None

    if decision is None:
        return None

    # Record for diagnostic commands when enabled.
    if plugin_cfg.get("trace", {}).get("enabled"):
        _TRACE.record(decision, session_key=session_key)

    payload: Dict[str, Any] = {
        "provider": decision.provider,
        "model": decision.model,
        "reasoning_effort": decision.reasoning_effort,
        "route_label": decision.route_label,
        "route_source": decision.route_source,
        "stack_name": decision.stack_name,
        "tier": decision.tier,
        "fallback_reason": decision.fallback_reason,
        "final_decision": True,
    }
    return _safe_overrides(payload)


# ---------------------------------------------------------------------------
# Manual/auto lock storage (lives on the gateway runner; we hold a
# weakref-shaped global so tests can patch the storage in/out)
# ---------------------------------------------------------------------------


def _session_locks_ref() -> Dict[str, Dict[str, str]]:
    """Return the active session-locks dict.

    The gateway registers itself with :func:`set_session_locks_store`.
    When no gateway is registered (CLI/dev/test), falls back to a
    process-global dict so unit tests work without touching the
    gateway.
    """
    global _SESSION_LOCKS_STORE
    if _SESSION_LOCKS_STORE is None:
        _SESSION_LOCKS_STORE = {}
    return _SESSION_LOCKS_STORE


_SESSION_LOCKS_STORE: Optional[Dict[str, Dict[str, str]]] = None


def set_session_locks_store(store: Optional[Dict[str, Dict[str, str]]]) -> None:
    """Wire the gateway's session-locks dict into the plugin.

    Called by ``gateway/run.py`` at startup.  Pass ``None`` to detach.
    """
    global _SESSION_LOCKS_STORE
    _SESSION_LOCKS_STORE = store


def set_manual_lock(session_key: str, *, model: str, provider: str, source: str = "user") -> ManualLock:
    return set_lock(_session_locks_ref(), session_key, model=model, provider=provider, source=source)


def clear_manual_lock(session_key: str) -> bool:
    return clear_lock(_session_locks_ref(), session_key)


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------


def register(ctx) -> None:
    """Register the adaptive-routing plugin surfaces."""
    ctx.register_hook(
        "resolve_turn_route",
        _resolve_turn_route_hook,
    )

    # CLI: a focused diagnostic that shows the new balanced scoring
    # trace for a given message.  Kept distinct from the legacy
    # ``adaptive-routing`` command to avoid breaking changes.
    def _diagnose_setup(parser):
        parser.add_argument("message", help="User message to classify")
        parser.add_argument("--provider", default="", help="Primary provider")
        parser.add_argument("--model", default="", help="Primary model")
        parser.add_argument("--policy", default="", help="Optional policy JSON")

    def _diagnose_handler(args):
        from .policy import DEFAULT_STACKS, build_quota_states, build_stacks
        stacks = build_stacks(None)
        # Best-effort: try to fetch quota for each provider in the stacks.
        providers = sorted({cand.provider for stack in stacks.values() for cand in stack.candidates.values()})
        quota_states = _quota_states_for(providers)
        features = extract_features(args.message)
        tier = classify_tier(features)
        effort = reasoning_effort_for(tier, features)
        decision = select_route(
            args.message,
            stacks=stacks,
            quota_states=quota_states,
        )
        if decision is None:
            return f"tier={tier} effort={effort} decision=<none>"
        breakdown = decision.score_breakdown or {}
        return "\n".join([
            f"tier: {decision.tier}",
            f"stack: {decision.stack_name}",
            f"effort: {decision.reasoning_effort}",
            f"provider: {decision.provider}",
            f"model: {decision.model}",
            f"label: {decision.route_label}",
            f"score_total: {breakdown.get('total', 0):.3f}",
            f"  quality_fit: {breakdown.get('quality_fit', 0):.3f}",
            f"  normalized_cost: {breakdown.get('normalized_cost', 0):.3f}",
            f"  quota_penalty: {breakdown.get('quota_penalty', 0):.3f}",
            f"  mismatch_penalty: {breakdown.get('mismatch_penalty', 0):.3f}",
            f"fallback_reason: {decision.fallback_reason or '-'}",
            f"final_decision: {decision.final_decision}",
        ])

    ctx.register_cli_command(
        name="route-diagnose",
        help="Run the new balanced scoring engine on a message",
        setup_fn=_diagnose_setup,
        handler_fn=_diagnose_handler,
        description="Balanced scoring trace for adaptive routing",
    )

    # CLI: route trace dump (only meaningful when ``trace.enabled``).
    def _trace_setup(parser):
        parser.add_argument("--clear", action="store_true", help="Clear trace buffer")

    def _trace_handler(args):
        if args.clear:
            _TRACE.clear()
            return "trace cleared"
        entries = _TRACE.snapshot()
        if not entries:
            return "trace is empty (enable plugins.adaptive_routing.trace.enabled to record)"
        lines: list[str] = []
        for i, e in enumerate(entries, 1):
            score = e.get("score") or {}
            total = score.get("total")
            score_str = f" total={total:.3f}" if isinstance(total, (int, float)) else ""
            lines.append(
                f"#{i} ts={e['ts']:.0f} session={e['session_key']!r} "
                f"stack={e['stack']} tier={e['tier']} effort={e['effort']} "
                f"provider={e['provider']} model={e['model']} label={e['label']} "
                f"source={e.get('route_source', 'adaptive')}{score_str}"
            )
        return "\n".join(lines)

    ctx.register_cli_command(
        name="route-trace",
        help="Show recent route decisions (requires trace.enabled=true)",
        setup_fn=_trace_setup,
        handler_fn=_trace_handler,
        description="Recent adaptive-routing decisions",
    )
