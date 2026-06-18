from __future__ import annotations

from agent.reasoning_policy import (
    CodexQuotaState,
    DEFAULT_REASONING_POLICY,
    decide_turn_route,
    fallback_chain_for_profile,
    format_route_footer,
    is_codex_quota_error,
)


def _policy(**overrides):
    policy = dict(DEFAULT_REASONING_POLICY)
    # Disable MiMo by default so existing Codex-centric tests keep working.
    policy["mimo_provider"] = ""
    policy.update(overrides)
    return policy


def _mimo_policy(**overrides):
    """Policy with MiMo enabled as primary."""
    policy = dict(DEFAULT_REASONING_POLICY)
    policy["mimo_provider"] = "xiaomi"
    policy.update(overrides)
    return policy


def _quota(percent: float) -> CodexQuotaState:
    return CodexQuotaState(percent_remaining=percent, reset_at=None, unavailable=False)


# ────────────────────────────── MiMo-first tests ──────────────────────────────


def test_mimo_tiny_uses_flash_low() -> None:
    decision = decide_turn_route(
        "Hi",
        primary_provider="openai-codex",
        primary_model="gpt-5.5",
        quota=_quota(95),
        policy=_mimo_policy(enabled=True),
    )

    assert decision.provider == "xiaomi"
    assert decision.model == "mimo-v2.5"
    assert decision.reasoning_effort == "low"
    assert decision.route_label == "mimo"
    assert format_route_footer(decision) == "mimo | low"


def test_mimo_easy_uses_flash_medium() -> None:
    decision = decide_turn_route(
        "Summarize this short config and tell me if anything looks wrong.",
        primary_provider="openai-codex",
        primary_model="gpt-5.5",
        quota=_quota(95),
        policy=_mimo_policy(enabled=True),
    )

    assert decision.provider == "xiaomi"
    assert decision.model == "mimo-v2.5"
    assert decision.reasoning_effort == "medium"
    assert decision.route_label == "mimo"
    assert format_route_footer(decision) == "mimo | medium"


def test_mimo_hard_uses_pro_xhigh() -> None:
    decision = decide_turn_route(
        "Implement multi-file refactor with migration steps and run integration tests.",
        primary_provider="openai-codex",
        primary_model="gpt-5.5",
        quota=_quota(80),
        policy=_mimo_policy(enabled=True),
    )

    assert decision.provider == "xiaomi"
    assert decision.model == "mimo-v2.5-pro"
    assert decision.reasoning_effort in {"high", "xhigh"}
    assert decision.route_label == "mimo"


def test_mimo_very_hard_uses_pro_xhigh() -> None:
    decision = decide_turn_route(
        "Debug this production outage, inspect the logs, patch the code, and run tests.",
        primary_provider="openai-codex",
        primary_model="gpt-5.5",
        quota=_quota(80),
        policy=_mimo_policy(enabled=True),
    )

    assert decision.provider == "xiaomi"
    assert decision.model == "mimo-v2.5-pro"
    assert decision.reasoning_effort == "xhigh"
    assert format_route_footer(decision) == "mimo-pro | xhigh"


def test_mimo_footer_flash_has_no_pro_suffix() -> None:
    decision = decide_turn_route(
        "thanks",
        primary_provider="openai-codex",
        primary_model="gpt-5.5",
        quota=_quota(95),
        policy=_mimo_policy(enabled=True),
    )
    assert format_route_footer(decision) == "mimo | low"


def test_fallback_chain_mimo_first_codex_second_deepseek_third() -> None:
    from agent.reasoning_policy import classify_task

    policy = _mimo_policy(enabled=True)
    profile = classify_task("Implement a multi-file refactor with tests.", policy)
    chain = fallback_chain_for_profile(policy, profile)

    providers = [e["provider"] for e in chain]
    assert providers[0] == "xiaomi"
    # Codex should be second
    assert "openai-codex" in providers
    assert providers.index("openai-codex") > providers.index("xiaomi")
    # DeepSeek should be last
    assert "deepseek" in providers
    assert providers.index("deepseek") > providers.index("openai-codex")


def test_fallback_chain_excludes_specified_provider() -> None:
    from agent.reasoning_policy import classify_task

    policy = _mimo_policy(enabled=True)
    profile = classify_task("Summarize this.", policy)

    # Excluding MiMo should start with Codex
    chain = fallback_chain_for_profile(policy, profile, exclude_provider="xiaomi")
    providers = [e["provider"] for e in chain]
    assert "xiaomi" not in providers
    assert providers[0] == "openai-codex"

    # Excluding Codex should skip it
    chain = fallback_chain_for_profile(policy, profile, exclude_provider="openai-codex")
    providers = [e["provider"] for e in chain]
    assert "openai-codex" not in providers
    assert providers[0] == "xiaomi"


def test_fallback_chain_easy_prefers_flash_then_pro() -> None:
    from agent.reasoning_policy import classify_task

    policy = _mimo_policy(enabled=True)
    profile = classify_task("Summarize this.", policy)
    chain = fallback_chain_for_profile(policy, profile)

    mimo_models = [e["model"] for e in chain if e["provider"] == "xiaomi"]
    assert mimo_models == ["mimo-v2.5", "mimo-v2.5-pro"]

    codex_models = [e["model"] for e in chain if e["provider"] == "openai-codex"]
    assert codex_models == ["gpt-5.4-mini", "gpt-5.5"]

    ds_models = [e["model"] for e in chain if e["provider"] == "deepseek"]
    assert ds_models == ["deepseek-v4-flash", "deepseek-v4-pro"]


def test_fallback_chain_hard_prefers_pro_then_flash() -> None:
    from agent.reasoning_policy import classify_task

    policy = _mimo_policy(enabled=True)
    profile = classify_task(
        "Implement adaptive quota-aware model routing with production tests.", policy
    )
    chain = fallback_chain_for_profile(policy, profile)

    mimo_models = [e["model"] for e in chain if e["provider"] == "xiaomi"]
    assert mimo_models == ["mimo-v2.5-pro", "mimo-v2.5"]

    codex_models = [e["model"] for e in chain if e["provider"] == "openai-codex"]
    assert codex_models == ["gpt-5.5", "gpt-5.4-mini"]


# ────────────────────────────── Codex tests (MiMo disabled) ──────────────────


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


def test_trivial_capital_question_does_not_match_api_inside_capital() -> None:
    decision = decide_turn_route(
        "What is the capital of England?",
        primary_provider="openai-codex",
        primary_model="gpt-5.5",
        quota=_quota(95),
        policy=_policy(enabled=True),
    )

    assert decision.profile.difficulty == "easy"
    assert decision.profile.score == 0
    assert decision.model == "gpt-5.4-mini"
    assert decision.reasoning_effort == "medium"


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


def test_disabled_policy_returns_primary_unchanged() -> None:
    decision = decide_turn_route(
        "Implement this feature with tests.",
        primary_provider="openai-codex",
        primary_model="gpt-5.5",
        quota=_quota(95),
        policy=_policy(enabled=False),
    )
    assert decision.provider == "openai-codex"
    assert decision.model == "gpt-5.5"
    assert decision.route_label == "codex"
