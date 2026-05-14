from __future__ import annotations

from agent.reasoning_policy import (
    CodexQuotaState,
    DEFAULT_REASONING_POLICY,
    decide_turn_route,
    format_route_footer,
    is_codex_quota_error,
)


def _policy(**overrides):
    policy = dict(DEFAULT_REASONING_POLICY)
    policy.update(overrides)
    return policy


def _quota(percent: float) -> CodexQuotaState:
    return CodexQuotaState(percent_remaining=percent, reset_at=None, unavailable=False)


def test_tiny_healthy_codex_uses_mini_low_and_compact_footer() -> None:
    decision = decide_turn_route(
        "Hi",
        primary_provider="openai-codex",
        primary_model="gpt-5.5",
        quota=_quota(95),
        policy=_policy(enabled=True),
    )

    assert decision.provider == "openai-codex"
    assert decision.model == "gpt-5.4-mini"
    assert decision.reasoning_effort == "low"
    assert decision.route_label == "codex"
    assert format_route_footer(decision) == "codex | mini-low"


def test_easy_healthy_codex_uses_mini_medium_to_avoid_low_reasoning_mistakes() -> None:
    decision = decide_turn_route(
        "Summarize this short config and tell me if anything looks wrong.",
        primary_provider="openai-codex",
        primary_model="gpt-5.5",
        quota=_quota(95),
        policy=_policy(enabled=True),
    )

    assert decision.provider == "openai-codex"
    assert decision.model == "gpt-5.4-mini"
    assert decision.reasoning_effort == "medium"
    assert format_route_footer(decision) == "codex | mini-medium"


def test_hard_planning_task_healthy_codex_stays_on_codex_with_xhigh_reasoning() -> None:
    decision = decide_turn_route(
        "Implement adaptive quota-aware model routing across the gateway, add tests, "
        "update config defaults, and verify the full diff.",
        primary_provider="openai-codex",
        primary_model="gpt-5.5",
        quota=_quota(80),
        policy=_policy(enabled=True),
    )

    assert decision.provider == "openai-codex"
    assert decision.model == "gpt-5.5"
    assert decision.reasoning_effort == "xhigh"
    assert format_route_footer(decision) == "codex | xhigh"


def test_low_quota_between_two_and_four_percent_still_prefers_codex_by_default() -> None:
    decision = decide_turn_route(
        "Implement a multi-file refactor with tests and migration steps.",
        primary_provider="openai-codex",
        primary_model="gpt-5.5",
        quota=_quota(3),
        policy=_policy(enabled=True, low_quota_hard_task_behavior="use_codex_until_error"),
    )

    assert decision.provider == "openai-codex"
    assert decision.fallback_reason == ""


def test_emergency_quota_routes_hard_tasks_to_deepseek_pro_but_tiny_tasks_to_codex() -> None:
    hard = decide_turn_route(
        "Debug this production outage, inspect the logs, patch the code, and run tests.",
        primary_provider="openai-codex",
        primary_model="gpt-5.5",
        quota=_quota(1),
        policy=_policy(enabled=True),
    )
    tiny = decide_turn_route(
        "thanks",
        primary_provider="openai-codex",
        primary_model="gpt-5.5",
        quota=_quota(1),
        policy=_policy(enabled=True),
    )

    assert hard.provider == "deepseek"
    assert hard.model == "deepseek-v4-pro"
    assert hard.route_label == "deepseek-v4-pro"
    assert tiny.provider == "openai-codex"
    assert tiny.reasoning_effort == "low"


def test_codex_quota_error_fallback_selects_flash_for_easy_and_pro_for_hard() -> None:
    easy = decide_turn_route(
        "Summarize this sentence.",
        primary_provider="openai-codex",
        primary_model="gpt-5.5",
        quota=_quota(95),
        policy=_policy(enabled=True),
        codex_error="Rate limit exceeded for your Codex account quota",
    )
    hard = decide_turn_route(
        "Perform a security review of this repository and propose fixes with tests.",
        primary_provider="openai-codex",
        primary_model="gpt-5.5",
        quota=_quota(95),
        policy=_policy(enabled=True),
        codex_error="context deadline: usage limit reached",
    )

    assert easy.provider == "deepseek"
    assert easy.model == "deepseek-v4-flash"
    assert easy.route_label == "deepseek-v4-flash"
    assert hard.provider == "deepseek"
    assert hard.model == "deepseek-v4-pro"
    assert hard.route_label == "deepseek-v4-pro"


def test_quota_error_detector_is_specific_to_codex_usage_exhaustion() -> None:
    assert is_codex_quota_error("Rate limit exceeded: usage limit reached")
    assert is_codex_quota_error("You have exceeded your Codex quota")
    assert not is_codex_quota_error("temporary upstream 502 bad gateway")
    assert not is_codex_quota_error("invalid api key")
