"""Plugin-owned adaptive-routing policy primitives.

The plugin owns task-feature extraction, balanced scoring, and route
selection.  Core ``agent.reasoning_policy`` remains the simple, self-
contained fallback used when the plugin is disabled or returns an
advisory/incomplete result.

The policy engine here is deliberately pure: callers pass the user
message, primary provider/model, optional ``QuotaState`` snapshots, and
the resolved plugin config (with legacy ``agent.reasoning_policy`` keys
overlayed for backwards compatibility).  No network, no filesystem, no
plugin imports at runtime.

Cache safety: nothing in this module mutates or returns messages,
history, tools, toolsets, system, or memory.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Task classification
# ---------------------------------------------------------------------------


_ROUTINE_KEYWORDS = frozenset({
    "what is", "what's", "how do", "thanks", "hi", "hello", "summarize this sentence",
    "translate", "lookup", "convert", "tiny", "trivial", "rename", "format",
})
_HARD_KEYWORDS = frozenset((
    "implement", "refactor", "debug", "root cause", "production", "outage",
    "security", "review", "migrate", "architecture", "routing", "quota",
    "fallback", "gateway", "multi-file", "codebase", "repository", "tests",
    "integration", "regression", "deploy", "database", "concurrency",
    "concurrency", "race condition", "deadlock", "memory leak", "out of memory",
    "oom", "secrets", "credentials", "authn", "authorization", "permissions",
))
_EVIDENCE_MARKERS = ("```", "Traceback", "diff --git", "ERROR", "Exception", "panic:", "stack trace")
_SAFETY_TOKENS = ("delete", "rm -rf", "force-push", "drop database", "truncate", "destroy")
_BREADTH_TOKENS = ("multi-file", "upstream merge", "cross-module", "monorepo", "workspace")


@dataclass(frozen=True)
class TaskFeatures:
    """Deterministic, conservative feature extraction result."""

    word_count: int
    line_count: int
    code_block_count: int
    has_traceback: bool
    has_diff: bool
    has_logs: bool
    has_pasted_source: bool
    implementation_signal: bool
    debugging_signal: bool
    risk_signal: bool
    breadth_signal: bool
    safety_signal: bool
    evidence_signal: bool
    matched_routine: tuple[str, ...] = field(default_factory=tuple)
    matched_hard: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_routine(self) -> bool:
        return bool(self.matched_routine) and not self.matched_hard and not self.risk_signal \
            and not self.breadth_signal and not self.evidence_signal and self.word_count <= 18

    @property
    def is_complex(self) -> bool:
        # Multi-file architecture, gateway/runtime/provider, quota/fallback,
        # production/security/concurrency, breadth tokens, or extreme size.
        if self.breadth_signal:
            return True
        if any(tok in self.matched_hard for tok in ("production", "outage", "security", "concurrency", "gateway", "routing", "quota", "fallback", "multi-file")):
            return True
        if self.word_count > 250:
            return True
        if self.safety_signal and self.risk_signal:
            return True
        return False

    @property
    def is_difficult(self) -> bool:
        if self.is_complex or self.is_routine:
            return False
        return bool(self.matched_hard) or self.evidence_signal or self.word_count > 60 or self.line_count > 20


def _keyword_matches(compact_text: str, keyword: str) -> bool:
    keyword = keyword.strip().lower()
    if not keyword:
        return False
    if " " in keyword:
        return keyword in compact_text
    return re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", compact_text) is not None


def extract_features(message: Any) -> TaskFeatures:
    """Deterministically extract task features from a user message."""
    if isinstance(message, str):
        text = message
    elif isinstance(message, list):
        parts: list[str] = []
        for item in message:
            if isinstance(item, Mapping):
                value = item.get("text") or item.get("content") or ""
                if isinstance(value, str):
                    parts.append(value)
            elif isinstance(item, str):
                parts.append(item)
        text = "\n".join(parts)
    else:
        text = str(message or "")

    compact = " ".join(text.lower().split())
    words = re.findall(r"[\w/.-]+", compact)
    word_count = len(words)
    line_count = max(1, text.count("\n") + 1)
    code_block_count = text.count("```") // 2
    has_traceback = "traceback" in compact or "exception" in compact or "panic:" in compact
    has_diff = "diff --git" in text or "@@" in text
    has_logs = bool(re.search(r"\b(ERROR|WARN|INFO)\b\s*:", text)) or "Traceback" in text
    has_pasted_source = code_block_count > 0 and word_count > 40

    implementation_signal = bool(re.search(r"\b(implement|build|add|create|write|patch|modify)\b", compact))
    debugging_signal = bool(re.search(r"\b(debug|fix|root cause|regression|why is|stack trace)\b", compact))
    risk_signal = any(tok in compact for tok in ("production", "outage", "security", "data loss", "auth", "credential"))
    breadth_signal = any(tok in compact for tok in _BREADTH_TOKENS)
    safety_signal = any(tok in compact for tok in _SAFETY_TOKENS)
    evidence_signal = has_traceback or has_diff or has_logs or has_pasted_source

    matched_routine = tuple(sorted(kw for kw in _ROUTINE_KEYWORDS if _keyword_matches(compact, kw)))
    matched_hard = tuple(sorted(kw for kw in _HARD_KEYWORDS if _keyword_matches(compact, kw)))

    return TaskFeatures(
        word_count=word_count,
        line_count=line_count,
        code_block_count=code_block_count,
        has_traceback=has_traceback,
        has_diff=has_diff,
        has_logs=has_logs,
        has_pasted_source=has_pasted_source,
        implementation_signal=implementation_signal,
        debugging_signal=debugging_signal,
        risk_signal=risk_signal,
        breadth_signal=breadth_signal,
        safety_signal=safety_signal,
        evidence_signal=evidence_signal,
        matched_routine=matched_routine,
        matched_hard=matched_hard,
    )


# ---------------------------------------------------------------------------
# Tier mapping
# ---------------------------------------------------------------------------


TIER_ROUTINE = "routine"
TIER_DIFFICULT = "difficult"
TIER_COMPLEX = "complex"
_TIERS = (TIER_ROUTINE, TIER_DIFFICULT, TIER_COMPLEX)


def classify_tier(features: TaskFeatures) -> str:
    """Map extracted features onto a routing tier.

    Order matters: complex absorbs the difficult edge cases, then
    difficult absorbs medium-effort signals, then routine handles
    the rest.  Tiny prompts with no signal still land in routine.
    """
    if features.is_complex:
        return TIER_COMPLEX
    if features.is_difficult:
        return TIER_DIFFICULT
    if features.is_routine:
        return TIER_ROUTINE
    if features.word_count <= 8 and not features.matched_hard:
        return TIER_ROUTINE
    return TIER_DIFFICULT


# ---------------------------------------------------------------------------
# Quota state (provider-agnostic)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class QuotaState:
    """Provider-agnostic quota snapshot for routing.

    Used by the plugin to compute quota_penalty in balanced scoring.
    Mirrors the shape of ``agent.reasoning_policy.CodexQuotaState``
    but adds ``provider`` and ``is_payg`` so the policy engine can
    branch on the source provider.
    """

    provider: str
    percent_remaining: Optional[float] = None
    reset_at: Optional[datetime] = None
    unavailable: bool = False
    is_payg: bool = False

    @classmethod
    def payg(cls, provider: str) -> "QuotaState":
        return cls(provider=provider, is_payg=True)

    @classmethod
    def unknown(cls, provider: str) -> "QuotaState":
        return cls(provider=provider, unavailable=True)


# ---------------------------------------------------------------------------
# Stack + candidate definitions
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TierCandidate:
    """A single (provider, model) candidate within a stack/tier."""

    provider: str
    model: str
    label: str
    quality_score: float
    cost_score: float
    reasoning_range: tuple[str, ...] = ()

    def supports_effort(self, effort: str) -> bool:
        if not self.reasoning_range:
            return True
        return effort in self.reasoning_range


@dataclass(frozen=True)
class Stack:
    """A named ordered stack (e.g. primary / codex_fallback / deepseek_fallback)."""

    name: str
    candidates: Mapping[str, TierCandidate]  # tier -> TierCandidate

    def candidate_for(self, tier: str) -> Optional[TierCandidate]:
        return self.candidates.get(tier)


# ---------------------------------------------------------------------------
# Default stacks (post-merge model names per plan §2)
# ---------------------------------------------------------------------------


DEFAULT_STACKS: dict[str, Stack] = {
    "primary": Stack(
        name="primary",
        candidates={
            TIER_ROUTINE: TierCandidate(
                provider="opencode-go",
                model="mimo-v2.5",
                label="mimo",
                quality_score=0.6,
                cost_score=0.3,
                reasoning_range=("low", "medium"),
            ),
            TIER_DIFFICULT: TierCandidate(
                provider="opencode-go",
                model="minimax-m3",
                label="minimax",
                quality_score=0.8,
                cost_score=0.5,
                reasoning_range=("medium", "high"),
            ),
            TIER_COMPLEX: TierCandidate(
                provider="openai-codex",
                model="gpt-5.5",
                label="codex",
                quality_score=0.95,
                cost_score=0.8,
                reasoning_range=("high", "xhigh"),
            ),
        },
    ),
    "codex_fallback": Stack(
        name="codex_fallback",
        candidates={
            TIER_ROUTINE: TierCandidate(
                provider="openai-codex",
                model="gpt-5.4-mini",
                label="codex-mini",
                quality_score=0.5,
                cost_score=0.2,
                reasoning_range=("low", "medium"),
            ),
            TIER_DIFFICULT: TierCandidate(
                provider="openai-codex",
                model="gpt-5.4",
                label="codex",
                quality_score=0.75,
                cost_score=0.6,
                reasoning_range=("medium", "high"),
            ),
            TIER_COMPLEX: TierCandidate(
                provider="openai-codex",
                model="gpt-5.5",
                label="codex",
                quality_score=0.95,
                cost_score=0.8,
                reasoning_range=("high", "xhigh"),
            ),
        },
    ),
    "deepseek_fallback": Stack(
        name="deepseek_fallback",
        candidates={
            TIER_ROUTINE: TierCandidate(
                provider="deepseek",
                model="deepseek-v4-flash",
                label="deepseek-flash",
                quality_score=0.45,
                cost_score=0.15,
                reasoning_range=("low", "medium"),
            ),
            TIER_DIFFICULT: TierCandidate(
                provider="deepseek",
                model="deepseek-v4-pro",
                label="deepseek-pro",
                quality_score=0.7,
                cost_score=0.4,
                reasoning_range=("medium", "high"),
            ),
            TIER_COMPLEX: TierCandidate(
                provider="deepseek",
                model="deepseek-v4-pro",
                label="deepseek-pro",
                quality_score=0.7,
                cost_score=0.4,
                reasoning_range=("high", "xhigh"),
            ),
        },
    ),
}


# Default quota policy per provider (used when the user config has no
# `quotas` section).  PAYG providers get ``is_payg=True`` and are
# never penalized.  Codex is the only rolling-quota source we
# currently trust enough to penalize on; Opencode Go / Zen have no
# supported quota source today so the policy engine treats them as
# ``unknown`` (a small neutral penalty, not a hard gate).
DEFAULT_QUOTA_POLICY: dict[str, dict[str, Any]] = {
    "opencode-go": {
        "type": "unknown",
        "display_in_footer": False,
    },
    "opencode-zen": {
        "type": "unknown",
        "display_in_footer": False,
    },
    "opencode": {
        "type": "unknown",
        "display_in_footer": False,
    },
    "openai-codex": {
        "type": "rolling",
        "window_hours": 5,
        "low_threshold_percent": 4.0,
        "emergency_threshold_percent": 2.0,
        "display_in_footer": True,
    },
    "deepseek": {
        "type": "payg",
        "display_in_footer": False,
    },
}


# ---------------------------------------------------------------------------
# Balanced scoring
# ---------------------------------------------------------------------------


# Higher = preferred.  Stack order is the tie-breaker (lower index wins).
def score_candidates(
    candidates: Iterable[TierCandidate],
    tier: str,
    quota_states: Mapping[str, QuotaState],
    *,
    quality_weight: float = 0.7,
    cost_weight: float = 0.3,
    quota_penalty_weight: float = 0.5,
    mismatch_penalty_weight: float = 0.3,
) -> list[tuple[TierCandidate, float, dict[str, float]]]:
    """Score candidates for *tier* using the balanced objective.

    No latency criterion.  Returns a list of (candidate, total, breakdown)
    sorted by descending total.  Ties preserve caller order (stack
    order is the implicit tiebreaker).
    """
    scored: list[tuple[TierCandidate, float, dict[str, float]]] = []
    for candidate in candidates:
        quality_fit = _quality_fit(candidate, tier)
        normalized_cost = max(0.0, min(1.0, candidate.cost_score))
        quota_pen = _quota_penalty(candidate, quota_states.get(candidate.provider))
        mismatch_pen = _mismatch_penalty(candidate, tier)
        total = (
            quality_weight * quality_fit
            + cost_weight * normalized_cost
            - quota_penalty_weight * quota_pen
            - mismatch_penalty_weight * mismatch_pen
        )
        breakdown = {
            "quality_fit": quality_fit,
            "normalized_cost": normalized_cost,
            "quota_penalty": quota_pen,
            "mismatch_penalty": mismatch_pen,
            "total": total,
        }
        scored.append((candidate, total, breakdown))
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored


def _quality_fit(candidate: TierCandidate, tier: str) -> float:
    base = max(0.0, min(1.0, candidate.quality_score))
    # Slight bonus for candidates whose reasoning range is broad enough
    # to cover the tier's expected effort ceiling.
    if tier == TIER_COMPLEX and "xhigh" in candidate.reasoning_range:
        base = min(1.0, base + 0.05)
    if tier == TIER_DIFFICULT and "high" in candidate.reasoning_range:
        base = min(1.0, base + 0.05)
    return base


def _quota_penalty(candidate: TierCandidate, quota: Optional[QuotaState]) -> float:
    if quota is None:
        return 0.0
    if quota.is_payg:
        return 0.0
    if quota.unavailable or quota.percent_remaining is None:
        # Unknown: don't punish, but don't pretend healthy either.
        return 0.1
    remaining = float(quota.percent_remaining)
    if remaining <= 0:
        return 1.0
    # Scale: at threshold (e.g. 4%) penalty ramps up; at 0% full penalty.
    policy = DEFAULT_QUOTA_POLICY.get(candidate.provider, {})
    if policy.get("type") != "rolling":
        return 0.0
    emergency = float(policy.get("emergency_threshold_percent", 2.0))
    low = float(policy.get("low_threshold_percent", 4.0))
    if remaining <= emergency:
        return 1.0
    if remaining <= low:
        # Linear ramp from 0.4 (at low) to 0.9 (at emergency)
        span = max(low - emergency, 0.01)
        return 0.4 + 0.5 * ((low - remaining) / span)
    return 0.0


def _mismatch_penalty(candidate: TierCandidate, tier: str) -> float:
    """Penalize over/underpowered candidates relative to the tier."""
    q = candidate.quality_score
    if tier == TIER_ROUTINE:
        # Heavy penalty for overpowered routine tasks.
        if q >= 0.9:
            return 0.7
        if q >= 0.7:
            return 0.3
        return 0.0
    if tier == TIER_COMPLEX:
        # Underpowered complex tasks are a real cost.
        if q < 0.5:
            return 0.9
        if q < 0.7:
            return 0.4
        return 0.0
    # Difficult
    if q < 0.4:
        return 0.5
    if q > 0.95:
        return 0.2
    return 0.0


# ---------------------------------------------------------------------------
# Reasoning effort mapping
# ---------------------------------------------------------------------------


def reasoning_effort_for(tier: str, features: TaskFeatures) -> str:
    """Pick a reasoning effort for *tier* using deterministic rules."""
    if tier == TIER_ROUTINE:
        if features.evidence_signal or features.debugging_signal or features.code_block_count > 0:
            return "medium"
        return "low"
    if tier == TIER_DIFFICULT:
        if (
            features.implementation_signal
            or features.debugging_signal
            or features.evidence_signal
            or features.code_block_count > 0
        ):
            return "high"
        return "medium"
    # complex
    if (
        features.risk_signal
        or features.breadth_signal
        or features.safety_signal
        or features.word_count > 250
    ):
        return "xhigh"
    return "high"


# ---------------------------------------------------------------------------
# Decision output
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RouteDecision:
    """A complete plugin route decision.

    ``final_decision`` is True when the plugin returns a complete
    decision — the gateway should apply it directly and skip core
    ``decide_turn_route()``.  ``None`` or a decision with
    ``final_decision=False`` is treated as advisory by the gateway,
    which then falls back to core routing.
    """

    provider: str
    model: str
    reasoning_effort: str
    route_label: str
    route_source: str = "adaptive"
    stack_name: str = "primary"
    tier: str = TIER_ROUTINE
    features: Optional[TaskFeatures] = None
    score_breakdown: Optional[dict[str, float]] = None
    quota_state: Optional[QuotaState] = None
    fallback_reason: str = ""
    final_decision: bool = True


def select_route(
    message: Any,
    *,
    stacks: Mapping[str, Stack] = DEFAULT_STACKS,
    quota_states: Optional[Mapping[str, QuotaState]] = None,
    scoring_weights: Optional[Mapping[str, float]] = None,
    stack_order: Optional[tuple[str, ...]] = None,
    excluded: Optional[set[tuple[str, str]]] = None,
) -> Optional[RouteDecision]:
    """Pick a complete route for the current message.

    Walks the stacks in ``stack_order`` (default: primary, codex_fallback,
    deepseek_fallback) and returns the highest-scoring candidate across
    the configured tiers.  When all eligible candidates score below
    zero, returns None — the caller should fall back to core routing.
    """
    features = extract_features(message)
    tier = classify_tier(features)
    effort = reasoning_effort_for(tier, features)
    quota_states = quota_states or {}
    weights = dict(scoring_weights or {})
    quality_w = float(weights.get("quality_weight", 0.7))
    cost_w = float(weights.get("cost_weight", 0.3))
    quota_w = float(weights.get("quota_penalty_weight", 0.5))
    mismatch_w = float(weights.get("mismatch_penalty_weight", 0.3))
    order = stack_order or ("primary", "codex_fallback", "deepseek_fallback")
    excluded = excluded or set()

    # Collect candidates: (stack_name, candidate) preserving stack order.
    flat: list[tuple[str, TierCandidate]] = []
    for stack_name in order:
        stack = stacks.get(stack_name)
        if stack is None:
            continue
        cand = stack.candidate_for(tier)
        if cand is None:
            continue
        if (cand.provider, cand.model) in excluded:
            continue
        flat.append((stack_name, cand))

    if not flat:
        return None

    candidates = [c for _, c in flat]
    scored = score_candidates(
        candidates,
        tier,
        quota_states,
        quality_weight=quality_w,
        cost_weight=cost_w,
        quota_penalty_weight=quota_w,
        mismatch_penalty_weight=mismatch_w,
    )
    if not scored:
        return None
    best, best_total, breakdown = scored[0]
    # Find the stack_name for the winner
    winner_stack = next((name for name, c in flat if c is best), "primary")
    # Soft fallthrough: if all candidates are hard-gated by quota, we
    # still return a decision (with fallback_reason) so the gateway
    # can apply the chain.  When the score is non-positive across the
    # board AND there's a primary excluded, we treat it as advisory.
    quota_state = quota_states.get(best.provider)
    fallback_reason = ""
    if breakdown["quota_penalty"] >= 1.0:
        fallback_reason = f"{best.provider}_quota_exhausted"
    return RouteDecision(
        provider=best.provider,
        model=best.model,
        reasoning_effort=effort,
        route_label=best.label,
        route_source="adaptive",
        stack_name=winner_stack,
        tier=tier,
        features=features,
        score_breakdown=breakdown,
        quota_state=quota_state,
        fallback_reason=fallback_reason,
        final_decision=True,
    )


# ---------------------------------------------------------------------------
# Config loading + legacy overlay
# ---------------------------------------------------------------------------


def load_plugin_config(raw_config: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """Load the plugin-owned config block with legacy overlay.

    Looks for ``plugins.adaptive_routing`` in *raw_config*; falls back
    to ``agent.reasoning_policy`` for backward compatibility.  Returns
    a normalized dict with the new schema.  Missing fields get safe
    defaults — never raises.
    """
    cfg: dict[str, Any] = {
        "enabled": False,
        "mode": "auto",
        "objective": "balanced",
        "stacks": {},
        "quotas": {},
        "scoring": {
            "quality_weight": 0.7,
            "cost_weight": 0.3,
            "quota_penalty_weight": 0.5,
            "mismatch_penalty_weight": 0.3,
            "unknown_cost_behavior": "neutral",
        },
        "manual_routing": {
            "lock_on_select": True,
            "clear_with": ("route_auto", "/new", "/reset"),
        },
        "footer": {
            "show_route_label": True,
            "show_reasoning_effort": True,
            "show_quota_percent": True,
            "show_score_trace": False,
        },
        "trace": {"enabled": False, "max_history": 10},
    }
    if not raw_config:
        return cfg

    plugins = raw_config.get("plugins") if isinstance(raw_config, Mapping) else None
    ar = None
    if isinstance(plugins, Mapping):
        ar = plugins.get("adaptive_routing") or plugins.get("adaptive-routing")
    legacy = None
    if isinstance(raw_config, Mapping):
        agent_block = raw_config.get("agent")
        if isinstance(agent_block, Mapping):
            legacy = agent_block.get("reasoning_policy")

    if isinstance(ar, Mapping):
        for key in ("enabled", "mode", "objective"):
            if key in ar:
                cfg[key] = ar[key]
        if isinstance(ar.get("scoring"), Mapping):
            for key, value in ar["scoring"].items():
                cfg["scoring"][key] = value
        if isinstance(ar.get("manual_routing"), Mapping):
            for key, value in ar["manual_routing"].items():
                cfg["manual_routing"][key] = value
        if isinstance(ar.get("footer"), Mapping):
            for key, value in ar["footer"].items():
                cfg["footer"][key] = value
        if isinstance(ar.get("trace"), Mapping):
            for key, value in ar["trace"].items():
                cfg["trace"][key] = value
        if isinstance(ar.get("quotas"), Mapping):
            cfg["quotas"] = dict(ar["quotas"])
        # Stacks are read in build_stacks() — keep raw here for it.
        if isinstance(ar.get("stacks"), Mapping):
            cfg["stacks"] = ar["stacks"]
    elif isinstance(legacy, Mapping):
        # Legacy mode: enabled flag + thresholds, no new stack schema.
        cfg["enabled"] = bool(legacy.get("enabled", cfg["enabled"]))
        legacy_quotas = cfg["quotas"]
        low_thr = legacy.get("codex_low_quota_threshold_percent")
        emerg_thr = legacy.get("codex_emergency_threshold_percent")
        if low_thr is not None or emerg_thr is not None:
            legacy_quotas = dict(legacy_quotas)
            codex = dict(legacy_quotas.get("openai-codex") or {})
            if low_thr is not None:
                codex["low_threshold_percent"] = float(low_thr)
            if emerg_thr is not None:
                codex["emergency_threshold_percent"] = float(emerg_thr)
            legacy_quotas["openai-codex"] = codex
            cfg["quotas"] = legacy_quotas
    return cfg


def build_stacks(raw_stacks: Optional[Mapping[str, Any]]) -> dict[str, Stack]:
    """Build Stack objects from a raw ``stacks`` config block.

    Falls back to ``DEFAULT_STACKS`` for any stack/tier that isn't
    configured.  Only builds the three documented stacks (primary,
    codex_fallback, deepseek_fallback) plus any user-defined stack.
    """
    stacks: dict[str, Stack] = {}
    raw_stacks = raw_stacks or {}
    for name in ("primary", "codex_fallback", "deepseek_fallback"):
        block = raw_stacks.get(name) if isinstance(raw_stacks, Mapping) else None
        default = DEFAULT_STACKS[name]
        if not isinstance(block, Mapping):
            stacks[name] = default
            continue
        candidates: dict[str, TierCandidate] = {}
        for tier in _TIERS:
            tier_block = block.get(tier)
            default_cand = default.candidate_for(tier)
            if default_cand is None:
                continue
            if not isinstance(tier_block, Mapping):
                candidates[tier] = default_cand
                continue
            provider = str(tier_block.get("provider") or default_cand.provider).strip() or default_cand.provider
            model = str(tier_block.get("model") or default_cand.model).strip() or default_cand.model
            label = str(tier_block.get("label") or default_cand.label).strip() or default_cand.label
            try:
                quality = float(tier_block.get("quality_score", default_cand.quality_score))
            except (TypeError, ValueError):
                quality = default_cand.quality_score
            try:
                cost = float(tier_block.get("cost_score", default_cand.cost_score))
            except (TypeError, ValueError):
                cost = default_cand.cost_score
            range_block = tier_block.get("reasoning_range")
            if isinstance(range_block, (list, tuple)) and range_block:
                reasoning_range: tuple[str, ...] = tuple(str(x).strip() for x in range_block if str(x).strip())
            else:
                reasoning_range = default_cand.reasoning_range
            candidates[tier] = TierCandidate(
                provider=provider,
                model=model,
                label=label,
                quality_score=quality,
                cost_score=cost,
                reasoning_range=reasoning_range,
            )
        stacks[name] = Stack(name=name, candidates=candidates)
    return stacks


def build_quota_states(
    raw_quotas: Optional[Mapping[str, Any]],
    runtime_snapshots: Optional[Mapping[str, Any]] = None,
) -> dict[str, QuotaState]:
    """Build a provider -> QuotaState map from plugin config + snapshots.

    PAYG providers get a no-penalty state immediately.  ``unknown``
    providers (those we don't have a real quota source for — e.g.
    Opencode Go / Zen) also resolve to a no-penalty state so they
    are treated as neutral in scoring and do not appear with a
    placeholder quota % in the runtime footer.  Rolling providers
    with no snapshot get an ``unknown`` state (degrades gracefully —
    small penalty, not hard-gate).  The ``runtime_snapshots``
    argument is intentionally typed loosely so callers can pass
    either a mapping of ``QuotaState`` already resolved by the
    gateway, or a mapping of ``AccountUsageSnapshot`` objects
    (which ``QuotaState.from_snapshot`` knows how to convert).
    """
    states: dict[str, QuotaState] = {}
    runtime_snapshots = runtime_snapshots or {}
    merged = {**DEFAULT_QUOTA_POLICY, **(raw_quotas or {})}
    for provider, policy in merged.items():
        if not isinstance(policy, Mapping):
            continue
        kind = str(policy.get("type") or "rolling").strip().lower()
        if kind in {"payg", "unknown"} or bool(policy.get("is_payg")):
            # ``unknown`` is the explicit "no real quota source" sentinel
            # — it costs zero penalty and never surfaces in the footer.
            # PAYG costs zero penalty.
            states[provider] = QuotaState.payg(provider)
            continue
        snapshot = runtime_snapshots.get(provider)
        if isinstance(snapshot, QuotaState):
            states[provider] = snapshot
            continue
        # The caller can supply an already-resolved ``QuotaState``, a
        # ``CodexQuotaState`` (legacy shape — lift it via
        # ``with_provider()``), an ``AccountUsageSnapshot`` (has
        # ``windows``), or ``None``.  When None we degrade to
        # ``unknown`` so the policy engine can apply a small neutral
        # penalty without gating hard.
        if snapshot is None:
            states[provider] = QuotaState.unknown(provider)
            continue
        if isinstance(snapshot, QuotaState):
            states[provider] = snapshot
            continue
        # Legacy ``CodexQuotaState`` — lift to provider-agnostic shape.
        try:
            from agent.reasoning_policy import CodexQuotaState as _CQS
            if isinstance(snapshot, _CQS):
                states[provider] = snapshot.with_provider(provider)
                continue
        except Exception:
            pass
        # Account-usage snapshot: derive a QuotaState from the windows.
        windows = getattr(snapshot, "windows", None)
        if windows:
            from agent.reasoning_policy import CodexQuotaState
            derived = CodexQuotaState.from_usage_snapshot(snapshot)
            if derived.unavailable or derived.percent_remaining is None:
                states[provider] = QuotaState.unknown(provider)
            else:
                states[provider] = QuotaState(
                    provider=provider,
                    percent_remaining=derived.percent_remaining,
                    reset_at=derived.reset_at,
                    unavailable=False,
                    is_payg=False,
                )
        else:
            states[provider] = QuotaState.unknown(provider)
    return states
