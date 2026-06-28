"""Tests for the plugin-owned adaptive-routing policy engine.

Covers the deterministic feature extractor, tier classification,
balanced scoring, route selection, manual/auto lock helpers, and the
new ``final_decision`` hook contract.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import types
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parents[3]


def _import_policy():
    """Import the plugin's policy module under a stable module name."""
    module_name = "plugins.adaptive_routing.policy"
    if module_name in sys.modules:
        return sys.modules[module_name]
    init_path = _REPO_ROOT / "plugins" / "adaptive-routing" / "policy.py"
    spec = importlib.util.spec_from_file_location(module_name, init_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _import_plugin_pkg():
    """Import the plugin package init under a stable module name."""
    module_name = "plugins.adaptive_routing"
    if module_name in sys.modules:
        return sys.modules[module_name]
    init_path = _REPO_ROOT / "plugins" / "adaptive-routing" / "__init__.py"
    spec = importlib.util.spec_from_file_location(module_name, init_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _import_manual_lock():
    """Import the plugin's manual_lock submodule under a stable module name."""
    module_name = "plugins.adaptive_routing.manual_lock"
    if module_name in sys.modules:
        return sys.modules[module_name]
    init_path = _REPO_ROOT / "plugins" / "adaptive-routing" / "manual_lock.py"
    spec = importlib.util.spec_from_file_location(module_name, init_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def policy():
    return _import_policy()


@pytest.fixture
def plugin_pkg():
    return _import_plugin_pkg()


# ---------------------------------------------------------------------------
# Feature extraction
# ---------------------------------------------------------------------------


class TestExtractFeatures:
    def test_tiny_message_has_low_word_count(self, policy):
        f = policy.extract_features("Hi")
        assert f.word_count <= 4
        assert f.line_count == 1
        assert f.code_block_count == 0

    def test_routine_keywords_detected(self, policy):
        f = policy.extract_features("What is the capital of France?")
        assert "what is" in f.matched_routine

    def test_hard_keywords_detected(self, policy):
        f = policy.extract_features("Implement a multi-file refactor with tests")
        assert any("implement" in k or "multi-file" in k for k in f.matched_hard)
        assert f.breadth_signal is True

    def test_traceback_marker_detected(self, policy):
        text = "Got this traceback when running tests:\n```\nTraceback (most recent call last)...\n```"
        f = policy.extract_features(text)
        assert f.has_traceback is True
        assert f.code_block_count >= 1
        assert f.evidence_signal is True

    def test_diff_marker_detected(self, policy):
        f = policy.extract_features("diff --git a/foo b/foo\n@@ ...")
        assert f.has_diff is True

    def test_safety_signal_detected(self, policy):
        f = policy.extract_features("rm -rf the production data directory")
        assert f.safety_signal is True
        assert f.risk_signal is True


# ---------------------------------------------------------------------------
# Tier classification
# ---------------------------------------------------------------------------


class TestClassifyTier:
    def test_routine_for_tiny_prompt(self, policy):
        f = policy.extract_features("Hi")
        assert policy.classify_tier(f) == policy.TIER_ROUTINE

    def test_routine_for_capital_question(self, policy):
        f = policy.extract_features("What is the capital of France?")
        assert policy.classify_tier(f) == policy.TIER_ROUTINE

    def test_difficult_for_implementation(self, policy):
        f = policy.extract_features("Implement a new retry helper and add unit tests")
        assert policy.classify_tier(f) == policy.TIER_DIFFICULT

    def test_complex_for_multi_file_production(self, policy):
        f = policy.extract_features("Production outage in the gateway. Multi-file root cause across runtime, provider, and quota fallback paths.")
        assert policy.classify_tier(f) == policy.TIER_COMPLEX


# ---------------------------------------------------------------------------
# Reasoning effort mapping
# ---------------------------------------------------------------------------


class TestReasoningEffort:
    def test_routine_defaults_to_low(self, policy):
        f = policy.extract_features("Hi")
        assert policy.reasoning_effort_for(policy.TIER_ROUTINE, f) == "low"

    def test_routine_with_code_block_uses_medium(self, policy):
        f = policy.extract_features("Look at this code:\n```\nprint(1)\n```")
        assert policy.reasoning_effort_for(policy.TIER_ROUTINE, f) == "medium"

    def test_difficult_defaults_to_medium(self, policy):
        f = policy.extract_features("What's a good way to organize helper functions?")
        assert policy.reasoning_effort_for(policy.TIER_DIFFICULT, f) == "medium"

    def test_complex_defaults_to_high(self, policy):
        f = policy.extract_features("Production outage in the gateway. Multi-file root cause.")
        assert policy.reasoning_effort_for(policy.TIER_COMPLEX, f) == "xhigh"

    def test_complex_with_risk_uses_xhigh(self, policy):
        f = policy.extract_features("Production outage in the gateway. Multi-file root cause.")
        assert policy.reasoning_effort_for(policy.TIER_COMPLEX, f) == "xhigh"


# ---------------------------------------------------------------------------
# Balanced scoring
# ---------------------------------------------------------------------------


class TestScoreCandidates:
    def test_no_latency_in_breakdown(self, policy):
        candidates = list(policy.DEFAULT_STACKS["primary"].candidates.values())
        scored = policy.score_candidates(candidates, policy.TIER_ROUTINE, {})
        for _cand, _total, breakdown in scored:
            assert "latency" not in breakdown
            assert all("latency" not in k for k in breakdown)

    def test_higher_quality_wins_for_complex(self, policy):
        candidates = [
            policy.TierCandidate("a", "m1", "l1", 0.5, 0.3, ("low", "medium")),
            policy.TierCandidate("b", "m2", "l2", 0.95, 0.8, ("high", "xhigh")),
        ]
        scored = policy.score_candidates(candidates, policy.TIER_COMPLEX, {})
        assert scored[0][0].provider == "b"

    def test_payg_provider_has_zero_quota_penalty(self, policy):
        quota = policy.QuotaState.payg("deepseek")
        candidate = policy.TierCandidate("deepseek", "deepseek-v4-flash", "ds", 0.45, 0.15, ("low",))
        _cand, _total, breakdown = policy.score_candidates(
            [candidate], policy.TIER_ROUTINE, {"deepseek": quota},
        )[0]
        assert breakdown["quota_penalty"] == 0.0

    def test_low_remaining_quota_increases_penalty(self, policy):
        healthy = policy.QuotaState(provider="openai-codex", percent_remaining=80.0)
        low = policy.QuotaState(provider="openai-codex", percent_remaining=3.0)
        zero = policy.QuotaState(provider="openai-codex", percent_remaining=0.0)
        candidate = policy.TierCandidate("openai-codex", "gpt-5.5", "codex", 0.95, 0.8, ("high", "xhigh"))
        scored = policy.score_candidates(
            [candidate], policy.TIER_COMPLEX,
            {"openai-codex": healthy},
        )
        _cand, _total_h, breakdown_h = scored[0]
        scored = policy.score_candidates(
            [candidate], policy.TIER_COMPLEX,
            {"openai-codex": low},
        )
        _cand, _total_l, breakdown_l = scored[0]
        scored = policy.score_candidates(
            [candidate], policy.TIER_COMPLEX,
            {"openai-codex": zero},
        )
        _cand, _total_z, breakdown_z = scored[0]
        assert breakdown_h["quota_penalty"] < breakdown_l["quota_penalty"] < breakdown_z["quota_penalty"]
        assert breakdown_z["quota_penalty"] == 1.0

    def test_overpowered_candidate_penalized_for_routine(self, policy):
        overpowered = policy.TierCandidate("a", "m1", "l1", 0.95, 0.8, ("high", "xhigh"))
        routine = policy.TierCandidate("b", "m2", "l2", 0.5, 0.3, ("low", "medium"))
        scored = policy.score_candidates([overpowered, routine], policy.TIER_ROUTINE, {})
        overpowered_breakdown = next(b for c, _t, b in scored if c is overpowered)
        routine_breakdown = next(b for c, _t, b in scored if c is routine)
        assert overpowered_breakdown["mismatch_penalty"] > routine_breakdown["mismatch_penalty"]


# ---------------------------------------------------------------------------
# Route selection
# ---------------------------------------------------------------------------


class TestSelectRoute:
    def test_routine_picks_mimo_v2_5(self, policy):
        decision = policy.select_route("Hi", quota_states={})
        assert decision is not None
        assert decision.provider == "opencode-go"
        assert decision.model == "mimo-v2.5"
        assert decision.tier == policy.TIER_ROUTINE

    def test_difficult_picks_minimax_m3(self, policy):
        decision = policy.select_route(
            "Implement a new retry helper and add unit tests",
            quota_states={},
        )
        assert decision is not None
        assert decision.tier == policy.TIER_DIFFICULT
        assert decision.provider == "opencode-go"
        assert decision.model == "minimax-m3"

    def test_complex_picks_codex_5_5(self, policy):
        decision = policy.select_route(
            "Production outage in the gateway. Multi-file root cause across runtime, provider, and quota fallback paths.",
            quota_states={},
        )
        assert decision is not None
        assert decision.tier == policy.TIER_COMPLEX
        assert decision.provider == "openai-codex"
        assert decision.model == "gpt-5.5"

    def test_exhausted_provider_excluded(self, policy):
        quota = policy.QuotaState(provider="opencode-go", percent_remaining=0.0)
        decision = policy.select_route(
            "Hi",
            quota_states={"opencode-go": quota},
            excluded={("opencode-go", "mimo-v2.5")},
        )
        assert decision is not None
        # Either the next tier's provider or a different model in the
        # primary stack.  Either way, the exhausted provider/model pair
        # must not appear.
        assert (decision.provider, decision.model) != ("opencode-go", "mimo-v2.5")

    def test_decision_carries_final_decision_flag(self, policy):
        decision = policy.select_route("Hi", quota_states={})
        assert decision.final_decision is True

    def test_decision_carries_tier_and_stack_name(self, policy):
        decision = policy.select_route("Hi", quota_states={})
        assert decision.stack_name in {"primary", "codex_fallback", "deepseek_fallback"}


# ---------------------------------------------------------------------------
# Stack + quota builders
# ---------------------------------------------------------------------------


class TestBuildStacks:
    def test_default_stacks_when_config_empty(self, policy):
        stacks = policy.build_stacks(None)
        assert set(stacks) == {"primary", "codex_fallback", "deepseek_fallback"}
        for stack in stacks.values():
            for tier in (policy.TIER_ROUTINE, policy.TIER_DIFFICULT, policy.TIER_COMPLEX):
                assert stack.candidate_for(tier) is not None

    def test_user_config_overrides_model_name(self, policy):
        raw = {
            "primary": {
                "routine": {"provider": "opencode-go", "model": "custom-flash", "label": "cf"},
            },
        }
        stacks = policy.build_stacks(raw)
        routine = stacks["primary"].candidate_for(policy.TIER_ROUTINE)
        assert routine is not None
        assert routine.model == "custom-flash"
        # Difficult/complex keep the defaults since we only overrode routine.
        difficult = stacks["primary"].candidate_for(policy.TIER_DIFFICULT)
        assert difficult is not None
        assert difficult.model == policy.DEFAULT_STACKS["primary"].candidate_for(policy.TIER_DIFFICULT).model


class TestBuildQuotaStates:
    def test_payg_provider_returns_payg_state(self, policy):
        states = policy.build_quota_states({"deepseek": {"type": "payg"}})
        assert states["deepseek"].is_payg is True
        assert states["deepseek"].percent_remaining is None

    def test_rolling_provider_without_snapshot_unknown(self, policy):
        # Use openai-codex (the only rolling provider in the default
        # policy now that Opencode Go / Zen are ``unknown`` because
        # there is no supported quota source for them today).
        states = policy.build_quota_states({"openai-codex": {"type": "rolling"}})
        assert states["openai-codex"].is_payg is False
        assert states["openai-codex"].unavailable is True

    def test_default_opencode_go_is_payg_neutral(self, policy):
        """Opencode Go has no supported quota source today, so the
        default policy marks it ``unknown`` and ``build_quota_states``
        short-circuits to a no-penalty state (no 0.1 neutral penalty,
        no gating).  This is the "treat Opencode quota as unavailable
        / neutral" follow-up."""
        states = policy.build_quota_states({})
        assert states["opencode-go"].is_payg is True
        assert states["opencode-go"].percent_remaining is None
        assert states["opencode-go"].unavailable is False
        # opencode-zen and the legacy "opencode" alias resolve the
        # same way.
        assert states["opencode-zen"].is_payg is True
        assert states["opencode"].is_payg is True

    def test_unknown_provider_type_short_circuits_to_payg(self, policy):
        states = policy.build_quota_states({"my-provider": {"type": "unknown"}})
        assert states["my-provider"].is_payg is True

    def test_rolling_provider_with_codex_snapshot(self, policy):
        from agent.reasoning_policy import CodexQuotaState
        codex = CodexQuotaState(percent_remaining=72.0)
        states = policy.build_quota_states(
            {"openai-codex": {"type": "rolling"}},
            runtime_snapshots={"openai-codex": codex},
        )
        assert states["openai-codex"].percent_remaining == 72.0
        assert states["openai-codex"].is_payg is False


# ---------------------------------------------------------------------------
# Config loading + legacy overlay
# ---------------------------------------------------------------------------


class TestLoadPluginConfig:
    def test_disabled_by_default(self, policy):
        cfg = policy.load_plugin_config(None)
        assert cfg["enabled"] is False

    def test_legacy_overlay_carries_enabled(self, policy):
        raw = {"agent": {"reasoning_policy": {"enabled": True}}}
        cfg = policy.load_plugin_config(raw)
        assert cfg["enabled"] is True

    def test_legacy_overlay_carries_codex_thresholds(self, policy):
        raw = {
            "agent": {
                "reasoning_policy": {
                    "enabled": True,
                    "codex_low_quota_threshold_percent": 7.0,
                    "codex_emergency_threshold_percent": 3.0,
                },
            },
        }
        cfg = policy.load_plugin_config(raw)
        assert cfg["quotas"]["openai-codex"]["low_threshold_percent"] == 7.0
        assert cfg["quotas"]["openai-codex"]["emergency_threshold_percent"] == 3.0

    def test_new_schema_overrides_legacy(self, policy):
        raw = {
            "plugins": {
                "adaptive_routing": {
                    "enabled": True,
                    "mode": "auto",
                    "scoring": {"quality_weight": 0.9, "cost_weight": 0.1},
                },
            },
            "agent": {"reasoning_policy": {"enabled": True}},
        }
        cfg = policy.load_plugin_config(raw)
        assert cfg["enabled"] is True
        assert cfg["scoring"]["quality_weight"] == 0.9
        assert cfg["scoring"]["cost_weight"] == 0.1


# ---------------------------------------------------------------------------
# Manual/auto lock
# ---------------------------------------------------------------------------


class TestManualLock:
    def test_set_and_get_lock(self):
        ml = _import_manual_lock()
        locks: dict = {}
        assert ml.is_locked(locks, "k") is False
        ml.set_lock(locks, "k", model="gpt-5.5", provider="openai-codex")
        assert ml.is_locked(locks, "k") is True
        assert ml.get_lock(locks, "k") == {
            "model": "gpt-5.5",
            "provider": "openai-codex",
            "source": "user",
        }
        assert ml.clear_lock(locks, "k") is True
        assert ml.is_locked(locks, "k") is False
        # Clearing an absent lock returns False
        assert ml.clear_lock(locks, "k") is False

    def test_clear_with_targets(self):
        ml = _import_manual_lock()
        locks: dict = {}
        ml.set_lock(locks, "route_auto", model="m", provider="p")
        # Targets match the key by equality — different keys don't trigger.
        assert ml.clear_with_targets(locks, "route_auto", ("route_auto", "/new")) is True
        # After clearing, calling again is a no-op.
        assert ml.clear_with_targets(locks, "route_auto", ("route_auto",)) is False


# ---------------------------------------------------------------------------
# Hook contract: final_decision semantics
# ---------------------------------------------------------------------------


class TestHookContract:
    def _enable_policy(self):
        return {
            "enabled": True,
            "mode": "auto",
            "objective": "balanced",
            "stacks": {},
            "quotas": {},
            "scoring": {},
        }

    def test_disabled_policy_returns_none(self, plugin_pkg):
        result = plugin_pkg._resolve_turn_route_hook(
            user_message="Hi",
            primary_provider="opencode-go",
            primary_model="mimo-v2.5",
            session_key="",
            policy={"enabled": False},
        )
        assert result is None

    def test_returns_final_decision_when_enabled(self, plugin_pkg):
        result = plugin_pkg._resolve_turn_route_hook(
            user_message="Hi",
            primary_provider="opencode-go",
            primary_model="mimo-v2.5",
            session_key="",
            policy=self._enable_policy(),
        )
        assert result is not None
        assert result.get("final_decision") is True
        assert "provider" in result
        assert "model" in result
        assert "reasoning_effort" in result

    def test_lock_overrides_plugin_decision(self, plugin_pkg):
        locks: dict = {}
        from plugins.adaptive_routing.manual_lock import set_lock
        set_lock(locks, "sk", model="gpt-5.5", provider="openai-codex")
        plugin_pkg.set_session_locks_store(locks)
        try:
            result = plugin_pkg._resolve_turn_route_hook(
                user_message="Hi",
                primary_provider="opencode-go",
                primary_model="mimo-v2.5",
                session_key="sk",
                policy=self._enable_policy(),
            )
            assert result is not None
            assert result.get("route_source") == "manual"
            assert result["provider"] == "openai-codex"
            assert result["model"] == "gpt-5.5"
        finally:
            plugin_pkg.set_session_locks_store(None)

    def test_unset_locks_store_uses_default(self, plugin_pkg):
        plugin_pkg.set_session_locks_store(None)
        # No lock set, no error, just an adaptive decision.
        result = plugin_pkg._resolve_turn_route_hook(
            user_message="Hi",
            primary_provider="opencode-go",
            primary_model="mimo-v2.5",
            session_key="no-such-session",
            policy=self._enable_policy(),
        )
        assert result is not None
        assert result.get("final_decision") is True

    def test_hook_does_not_return_dangerous_keys(self, plugin_pkg):
        result = plugin_pkg._resolve_turn_route_hook(
            user_message="Hi",
            primary_provider="opencode-go",
            primary_model="mimo-v2.5",
            session_key="",
            policy=self._enable_policy(),
        )
        if result is not None:
            dangerous = {"messages", "history", "tools", "toolsets", "system", "memory"}
            assert not (set(result.keys()) & dangerous)

    def test_hook_handles_classify_error(self, plugin_pkg, monkeypatch):
        # Simulate a hard failure inside the policy engine.  The hook
        # imports the policy symbols via from-import in
        # ``__init__.py`` so we have to patch the binding as seen by
        # the plugin package, not the policy module directly.
        def _boom(*args, **kwargs):
            raise RuntimeError("classify failed")
        monkeypatch.setattr(plugin_pkg, "select_route", _boom)
        result = plugin_pkg._resolve_turn_route_hook(
            user_message="Hi",
            primary_provider="opencode-go",
            primary_model="mimo-v2.5",
            session_key="",
            policy=self._enable_policy(),
        )
        assert result is None
