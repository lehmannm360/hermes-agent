"""Adaptive reasoning and quota-aware model routing policy.

This module is deliberately pure and side-effect free: callers provide the
current user message, primary runtime, and any quota/error snapshot.  The policy
returns a small decision object that the gateway/CLI can apply without baking
network or config access into the classifier itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any, Mapping, Optional


_REASONING_ORDER = {"none": 0, "minimal": 1, "low": 2, "medium": 3, "high": 4, "xhigh": 5}
_REASONING_LEVELS = {"low", "medium", "high", "xhigh"}
_HARD_DIFFICULTIES = {"hard", "very_hard"}
_HARD_EFFORTS = {"high", "xhigh"}

_HARD_KEYWORDS = frozenset((
    "implement", "refactor", "debug", "root cause", "production", "outage",
    "security", "review", "migrate", "architecture", "routing", "quota",
    "fallback", "gateway", "multi-file", "codebase", "repository", "tests",
    "integration", "regression", "deploy", "api", "database", "concurrency",
))
_MEDIUM_KEYWORDS = frozenset((
    "plan", "design", "analyze", "compare", "summarize", "configure", "setup",
    "install", "explain", "why", "how", "calculate", "inspect", "check",
))

DEFAULT_REASONING_POLICY: dict[str, Any] = {
    "enabled": False,
    "fallback_autonomy": True,
    "codex_low_quota_threshold_percent": 4.0,
    "codex_emergency_threshold_percent": 2.0,
    "allow_simple_codex_below_low_threshold": True,
    # User preference: preserve Codex as long as possible between 2–4%; let
    # existing runtime fallback handle a real quota/rate-limit error.
    "low_quota_hard_task_behavior": "use_codex_until_error",
    "mimo_provider": "xiaomi",
    "mimo_flash_model": "mimo-v2.5",
    "mimo_pro_model": "mimo-v2.5-pro",
    "deepseek_provider": "deepseek",
    "deepseek_flash_model": "deepseek-v4-flash",
    "deepseek_pro_model": "deepseek-v4-pro",
    "codex_primary_model": "gpt-5.5",
    "codex_fast_model": "gpt-5.4-mini",
    "codex_model_by_difficulty": {
        "tiny": "gpt-5.4-mini",
        "easy": "gpt-5.4-mini",
        "medium": "gpt-5.5",
        "hard": "gpt-5.5",
        "very_hard": "gpt-5.5",
    },
    "reasoning": {
        "tiny": "low",
        "easy": "medium",
        "medium": "medium",
        "hard": "high",
        "very_hard": "xhigh",
    },
}


@dataclass(frozen=True)
class CodexQuotaState:
    """Codex quota state used by the router.

    ``percent_remaining`` is the most constrained usable remaining quota window
    (usually the minimum of session and weekly windows).  ``None`` means the
    usage endpoint was unavailable, in which case the policy keeps Codex.
    """

    percent_remaining: Optional[float] = None
    reset_at: Optional[datetime] = None
    unavailable: bool = False

    @classmethod
    def from_usage_snapshot(cls, snapshot: Any) -> "CodexQuotaState":
        if not snapshot or getattr(snapshot, "unavailable_reason", None):
            return cls(unavailable=True)
        remaining_values: list[float] = []
        reset_at: Optional[datetime] = None
        for window in getattr(snapshot, "windows", ()) or ():
            used = getattr(window, "used_percent", None)
            if used is None:
                continue
            try:
                remaining_values.append(max(0.0, 100.0 - float(used)))
            except (TypeError, ValueError):
                continue
            candidate_reset = getattr(window, "reset_at", None)
            if candidate_reset is not None and (reset_at is None or candidate_reset < reset_at):
                reset_at = candidate_reset
        if not remaining_values:
            return cls(unavailable=True)
        return cls(percent_remaining=min(remaining_values), reset_at=reset_at, unavailable=False)


@dataclass(frozen=True)
class TaskProfile:
    difficulty: str
    reasoning_effort: str
    score: int
    huge_or_unsafe: bool = False


@dataclass(frozen=True)
class TurnRouteDecision:
    provider: str
    model: str
    reasoning_effort: str
    route_label: str
    profile: TaskProfile
    runtime_provider: Optional[str] = None
    fallback_reason: str = ""

    @property
    def reasoning_config(self) -> dict[str, str]:
        return {"effort": self.reasoning_effort}


def _policy_get(policy: Mapping[str, Any], key: str) -> Any:
    if key in policy:
        return policy[key]
    return DEFAULT_REASONING_POLICY.get(key)


def _policy_reasoning(policy: Mapping[str, Any], difficulty: str) -> str:
    configured = _policy_get(policy, "reasoning")
    value = "medium"  # safe mid-tier default, matches _REASONING_ORDER
    if isinstance(configured, Mapping):
        value = str(configured.get(difficulty) or value).strip().lower()
    if value not in _REASONING_LEVELS:
        value = "medium"
    return value


def _keyword_matches(compact_text: str, keyword: str) -> bool:
    keyword = keyword.strip().lower()
    if not keyword:
        return False
    if " " in keyword:
        return keyword in compact_text
    return re.search(rf"(?<!\w){re.escape(keyword)}(?!\w)", compact_text) is not None


def classify_task(message: Any, policy: Mapping[str, Any] | None = None) -> TaskProfile:
    """Classify task complexity with deterministic, conservative heuristics."""
    policy = policy or DEFAULT_REASONING_POLICY
    text = _stringify_message(message)
    compact = " ".join(text.lower().split())
    words = re.findall(r"[\w/.-]+", compact)
    word_count = len(words)
    score = 0

    if word_count <= 4 and len(compact) <= 32:
        difficulty = "tiny"
        return TaskProfile(difficulty, _policy_reasoning(policy, difficulty), score=0)

    for kw in _HARD_KEYWORDS:
        if _keyword_matches(compact, kw):
            score += 2
    for kw in _MEDIUM_KEYWORDS:
        if _keyword_matches(compact, kw):
            score += 1
    if word_count > 80:
        score += 3
    elif word_count > 35:
        score += 2
    elif word_count > 15:
        score += 1
    if any(marker in text for marker in ("```", "Traceback", "diff --git", "ERROR", "Exception")):
        score += 3
    if re.search(r"\b(run|write|add|patch|modify|create|fix)\b", compact):
        score += 1

    huge_or_unsafe = score >= 8 or word_count > 120 or "production" in compact or "outage" in compact
    if score >= 7:
        difficulty = "very_hard"
    elif score >= 5:
        difficulty = "hard"
    elif score >= 2:
        difficulty = "medium"
    else:
        difficulty = "easy"
    return TaskProfile(difficulty, _policy_reasoning(policy, difficulty), score=score, huge_or_unsafe=huge_or_unsafe)


def is_codex_provider(provider: Optional[str]) -> bool:
    return (provider or "").strip().lower() in {"openai-codex", "codex"}


def is_mimo_provider(provider: Optional[str]) -> bool:
    return (provider or "").strip().lower() in {"xiaomi", "mimo"}


def is_codex_quota_error(error: Any) -> bool:
    """Return True for Codex usage/quota/rate exhaustion errors."""
    if not error:
        return False
    text = str(error).lower()
    if not any(token in text for token in ("quota", "usage limit", "rate limit", "rate_limit", "limit reached", "too many requests")):
        return False
    # Avoid treating auth/config problems as quota fallback triggers.
    if any(token in text for token in ("invalid api key", "unauthorized", "forbidden", "permission", "authentication")):
        return False
    return True


def decide_turn_route(
    message: Any,
    *,
    primary_provider: Optional[str] = None,
    primary_model: str = "",
    quota: Optional[CodexQuotaState] = None,
    policy: Mapping[str, Any] | None = None,
    codex_error: Any = None,
) -> TurnRouteDecision:
    """Choose provider/model and reasoning effort for one user turn."""
    policy = policy or DEFAULT_REASONING_POLICY
    profile = classify_task(message, policy)

    if not bool(_policy_get(policy, "enabled")):
        return _primary_decision(
            (primary_provider or "").strip(),
            (primary_model or "").strip(),
            profile,
        )

    # ── MiMo first (primary adaptive route when configured) ──
    if str(_policy_get(policy, "mimo_provider") or "").strip():
        return _mimo_decision(policy, profile)

    # ── Codex fallback (quota-aware, with DeepSeek fallback) ──
    provider = (primary_provider or "").strip()
    if is_codex_provider(provider):
        return _codex_route(policy, provider, profile, quota, codex_error)

    return _primary_decision(provider, (primary_model or "").strip(), profile)


def _is_hard_profile(profile: TaskProfile) -> bool:
    """True for tasks that warrant the pro/heavy model tier."""
    return profile.difficulty in _HARD_DIFFICULTIES or profile.reasoning_effort in _HARD_EFFORTS


def _cap_profile(profile: TaskProfile, max_effort: str) -> TaskProfile:
    effort = profile.reasoning_effort
    if _REASONING_ORDER.get(effort, 3) > _REASONING_ORDER.get(max_effort, 2):
        effort = max_effort
    return TaskProfile(profile.difficulty, effort, profile.score, profile.huge_or_unsafe)


def _codex_route(
    policy: Mapping[str, Any],
    provider: str,
    profile: TaskProfile,
    quota: Optional[CodexQuotaState],
    codex_error: Any,
) -> TurnRouteDecision:
    """Codex routing with quota-aware fallback to DeepSeek."""
    if is_codex_quota_error(codex_error):
        return _deepseek_decision(policy, profile, fallback_reason="codex_quota_error")

    q = quota or CodexQuotaState(unavailable=True)
    remaining = q.percent_remaining
    if remaining is None or q.unavailable:
        return _codex_decision(policy, provider, profile)

    low = float(_policy_get(policy, "codex_low_quota_threshold_percent") or 4.0)
    emergency = float(_policy_get(policy, "codex_emergency_threshold_percent") or 2.0)
    if remaining <= emergency:
        if profile.difficulty in {"tiny", "easy"} and bool(_policy_get(policy, "allow_simple_codex_below_low_threshold")):
            return _codex_decision(policy, provider, _cap_profile(profile, "low"))
        return _deepseek_decision(policy, profile, fallback_reason="codex_emergency_quota")
    if remaining <= low:
        behavior = str(_policy_get(policy, "low_quota_hard_task_behavior") or "use_codex_until_error").strip().lower()
        if behavior in {"fallback_if_unsafe", "fallback_huge", "fallback"} and profile.huge_or_unsafe:
            return _deepseek_decision(policy, profile, fallback_reason="codex_low_quota_huge_task")
    return _codex_decision(policy, provider, profile)


def _mimo_decision(policy: Mapping[str, Any], profile: TaskProfile) -> TurnRouteDecision:
    """Route to MiMo with difficulty-based model selection."""
    provider = str(_policy_get(policy, "mimo_provider") or "xiaomi").strip()
    return TurnRouteDecision(
        provider=provider,
        model=_mimo_model_for_profile(policy, profile),
        reasoning_effort=profile.reasoning_effort,
        route_label="mimo",
        profile=profile,
        runtime_provider=provider,
    )


def _mimo_model_for_profile(policy: Mapping[str, Any], profile: TaskProfile) -> str:
    """Choose the right MiMo variant for task difficulty."""
    configured = _policy_get(policy, "mimo_model_by_difficulty")
    if isinstance(configured, Mapping):
        candidate = str(configured.get(profile.difficulty) or "").strip()
        if candidate:
            return candidate
    if _is_hard_profile(profile):
        return str(_policy_get(policy, "mimo_pro_model") or "mimo-v2.5-pro").strip()
    return str(_policy_get(policy, "mimo_flash_model") or "mimo-v2.5").strip()


def _primary_decision(
    provider: str,
    model: str,
    profile: TaskProfile,
    *,
    route_label: Optional[str] = None,
) -> TurnRouteDecision:
    label = route_label or _route_label(provider, model)
    return TurnRouteDecision(
        provider=provider,
        model=model,
        reasoning_effort=profile.reasoning_effort,
        route_label=label,
        profile=profile,
        runtime_provider=provider,
    )


def _codex_decision(
    policy: Mapping[str, Any],
    provider: str,
    profile: TaskProfile,
) -> TurnRouteDecision:
    """Choose the cheapest safe Codex model for the task profile."""
    return _primary_decision(provider, _codex_model_for_profile(policy, profile), profile, route_label="codex")


def _codex_model_for_profile(
    policy: Mapping[str, Any],
    profile: TaskProfile,
) -> str:
    configured = _policy_get(policy, "codex_model_by_difficulty")
    if isinstance(configured, Mapping):
        candidate = str(configured.get(profile.difficulty) or "").strip()
        if candidate:
            return candidate
    if not _is_hard_profile(profile):
        fast = str(_policy_get(policy, "codex_fast_model") or "gpt-5.4-mini").strip()
        if fast:
            return fast
    return str(_policy_get(policy, "codex_primary_model") or "gpt-5.5").strip()


def _deepseek_decision(policy: Mapping[str, Any], profile: TaskProfile, *, fallback_reason: str) -> TurnRouteDecision:
    provider = str(_policy_get(policy, "deepseek_provider") or "deepseek").strip() or "deepseek"
    hard = _is_hard_profile(profile)
    model_key = "deepseek_pro_model" if hard else "deepseek_flash_model"
    model = str(_policy_get(policy, model_key) or ("deepseek-v4-pro" if hard else "deepseek-v4-flash")).strip()
    return TurnRouteDecision(
        provider=provider,
        model=model,
        reasoning_effort=profile.reasoning_effort,
        route_label=model,
        profile=profile,
        runtime_provider=provider,
        fallback_reason=fallback_reason,
    )


def _route_label(provider: Optional[str], model: Optional[str]) -> str:
    provider_norm = (provider or "").strip().lower()
    if provider_norm in {"openai-codex", "codex"}:
        return "codex"
    if provider_norm == "deepseek":
        return (model or "deepseek").rsplit("/", 1)[-1]
    if provider_norm in {"xiaomi", "mimo"}:
        return "mimo"
    return (model or provider or "model").rsplit("/", 1)[-1]


def format_route_footer(decision: TurnRouteDecision | Mapping[str, Any]) -> str:
    if isinstance(decision, TurnRouteDecision):
        label = decision.route_label
        effort = decision.reasoning_effort
        model = decision.model
        provider = decision.provider
    else:
        provider = decision.get("provider")
        model = decision.get("model")
        label = str(decision.get("route_label") or _route_label(provider, model))
        effort = str(decision.get("reasoning_effort") or decision.get("effort") or "").strip().lower()
    if not label or not effort:
        return ""
    model_tail = str(model or "").rsplit("/", 1)[-1].lower()
    if is_codex_provider(str(provider or "")) and "mini" in model_tail:
        effort = f"mini-{effort}"
    elif is_mimo_provider(str(provider or "")) and model_tail.endswith("-pro"):
        label = "mimo-pro"
    return f"{label} | {effort}"


def fallback_chain_for_profile(policy: Mapping[str, Any], profile: TaskProfile, *, exclude_provider: str = "") -> list[dict[str, str]]:
    """Return an ordered fallback chain for runtime fallback, excluding *exclude_provider*."""
    hard = _is_hard_profile(profile)
    chain: list[dict[str, str]] = []
    skip = exclude_provider.strip().lower()

    # ── MiMo tier (first priority) ──
    mimo_provider = str(_policy_get(policy, "mimo_provider") or "").strip()
    if mimo_provider and mimo_provider.lower() != skip:
        mimo_flash = str(_policy_get(policy, "mimo_flash_model") or "mimo-v2.5").strip()
        mimo_pro = str(_policy_get(policy, "mimo_pro_model") or "mimo-v2.5-pro").strip()
        ordered = [mimo_pro, mimo_flash] if hard else [mimo_flash, mimo_pro]
        chain.extend({"provider": mimo_provider, "model": m} for m in ordered if m)

    # ── Codex tier (second priority) ──
    codex_fast = str(_policy_get(policy, "codex_fast_model") or "gpt-5.4-mini").strip()
    codex_primary = str(_policy_get(policy, "codex_primary_model") or "gpt-5.5").strip()
    if skip != "openai-codex":
        if hard:
            chain.extend({"provider": "openai-codex", "model": m} for m in [codex_primary, codex_fast] if m)
        else:
            chain.extend({"provider": "openai-codex", "model": m} for m in [codex_fast, codex_primary] if m)

    # ── DeepSeek tier (third priority) ──
    ds_provider = str(_policy_get(policy, "deepseek_provider") or "deepseek").strip() or "deepseek"
    ds_flash = str(_policy_get(policy, "deepseek_flash_model") or "deepseek-v4-flash").strip()
    ds_pro = str(_policy_get(policy, "deepseek_pro_model") or "deepseek-v4-pro").strip()
    ordered = [ds_pro, ds_flash] if hard else [ds_flash, ds_pro]
    if ds_provider.lower() != skip:
        chain.extend({"provider": ds_provider, "model": m} for m in ordered if m)

    return chain


def _stringify_message(message: Any) -> str:
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        parts: list[str] = []
        for item in message:
            if isinstance(item, Mapping):
                value = item.get("text") or item.get("content") or ""
                if isinstance(value, str):
                    parts.append(value)
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return str(message or "")
