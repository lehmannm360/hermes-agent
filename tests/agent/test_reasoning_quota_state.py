"""Tests for the provider-agnostic ``QuotaState`` added to
``agent.reasoning_policy``.

The class is additive: ``CodexQuotaState`` is preserved for backwards
compatibility with the existing core policy, and ``CodexQuotaState.with_provider()``
lifts a Codex snapshot into the new shape.
"""

from __future__ import annotations

from datetime import datetime, timezone

from agent.reasoning_policy import CodexQuotaState, QuotaState


class TestQuotaState:
    def test_payg_factory(self):
        state = QuotaState.payg("deepseek")
        assert state.provider == "deepseek"
        assert state.is_payg is True
        assert state.percent_remaining is None
        assert state.unavailable is False

    def test_unknown_factory(self):
        state = QuotaState.unknown("opencode-go")
        assert state.provider == "opencode-go"
        assert state.is_payg is False
        assert state.unavailable is True
        assert state.percent_remaining is None


class TestCodexQuotaStateWithProvider:
    def test_with_provider_lifts_to_quota_state(self):
        codex = CodexQuotaState(percent_remaining=72.0)
        lifted = codex.with_provider("openai-codex")
        assert isinstance(lifted, QuotaState)
        assert lifted.provider == "openai-codex"
        assert lifted.percent_remaining == 72.0
        assert lifted.unavailable is False
        assert lifted.is_payg is False

    def test_with_provider_preserves_unavailable(self):
        codex = CodexQuotaState(unavailable=True)
        lifted = codex.with_provider("opencode-go")
        assert lifted.unavailable is True
        assert lifted.percent_remaining is None
        assert lifted.provider == "opencode-go"

    def test_with_provider_preserves_reset_at(self):
        reset = datetime(2026, 6, 28, 12, 0, 0, tzinfo=timezone.utc)
        codex = CodexQuotaState(percent_remaining=10.0, reset_at=reset)
        lifted = codex.with_provider("openai-codex")
        assert lifted.reset_at == reset
