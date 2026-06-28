"""Tests for the runtime-footer quota display and route_source.

The provider-agnostic footer:
- OpenAI OAuth (Codex) keeps its existing 5-hour quota % behavior
- Opencode Go / Opencode Zen intentionally expose NO quota indicator
  in the footer (no supported quota source today).  Even when the
  gateway passes a value, the footer omits it — this regression
  covers the "no Opencode quota indicator" follow-up.
- DeepSeek PAYG omits quota %
- ``route_source`` flows through the field stack when provided
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from gateway.runtime_footer import (
    _provider_shows_quota_in_footer,
    _route_reasoning_label,
    build_footer_line,
    format_runtime_footer,
)


# ---------------------------------------------------------------------------
# _provider_shows_quota_in_footer
# ---------------------------------------------------------------------------


class TestProviderShowsQuota:
    @pytest.mark.parametrize("provider", ["openai-codex", "codex"])
    def test_rolling_providers_show_quota(self, provider):
        assert _provider_shows_quota_in_footer(provider) is True

    @pytest.mark.parametrize(
        "provider",
        # Opencode Go / Opencode Zen intentionally opt out of the
        # footer quota indicator — no supported quota source today.
        # DeepSeek is PAYG.  Everything else is unknown.
        ["opencode-go", "opencode", "deepseek", "anthropic", "openrouter", "moa", ""],
    )
    def test_opencode_and_payg_or_other_providers_omit_quota(self, provider):
        assert _provider_shows_quota_in_footer(provider) is False


# ---------------------------------------------------------------------------
# _route_reasoning_label — provider matrix
# ---------------------------------------------------------------------------


class TestRouteReasoningLabelProviderMatrix:
    def test_codex_with_quota(self):
        out = _route_reasoning_label(
            provider="openai-codex",
            model="gpt-5.4-mini",
            reasoning_effort="low",
            route_label="codex",
            codex_quota_used_percent=70.2,
        )
        assert out == "codex | mini-low | 70%"

    def test_opencode_go_without_quota(self):
        """Opencode Go never shows a quota % in the footer, even when
        the caller passes a value.  The footer must drop the
        trailing quota field so the line stays clean."""
        out = _route_reasoning_label(
            provider="opencode-go",
            model="mimo-v2.5",
            reasoning_effort="low",
            route_label="mimo",
            codex_quota_used_percent=42.0,
        )
        assert out == "mimo | low"
        assert "42" not in out
        assert "%" not in out

    def test_opencode_go_without_quota_value(self):
        """When no quota value is passed the footer naturally omits
        it (the provider opts out of the field entirely)."""
        out = _route_reasoning_label(
            provider="opencode-go",
            model="mimo-v2.5",
            reasoning_effort="low",
            route_label="mimo",
        )
        assert out == "mimo | low"

    def test_opencode_zen_without_quota(self):
        """Opencode Zen (alias of opencode-zen) behaves the same
        as Opencode Go: no quota indicator in the footer."""
        out = _route_reasoning_label(
            provider="opencode",
            model="gpt-5.4",
            reasoning_effort="high",
            route_label="codex",
            codex_quota_used_percent=99.0,  # ignored — no opencode quota
        )
        assert out == "codex | high"
        assert "99" not in out

    def test_deepseek_payg_omits_quota_even_when_value_provided(self):
        out = _route_reasoning_label(
            provider="deepseek",
            model="deepseek-v4-flash",
            reasoning_effort="low",
            route_label="deepseek-flash",
            codex_quota_used_percent=99.0,  # ignored — PAYG
        )
        assert out == "deepseek-flash | low"
        assert "99" not in out

    def test_route_source_does_not_change_visible_text(self):
        # The route_source is propagated through but the visible pipe text
        # is unchanged — the source would surface through a separate field
        # if the user enables it.
        out = _route_reasoning_label(
            provider="opencode-go",
            model="mimo-v2.5",
            reasoning_effort="low",
            route_label="mimo",
            route_source="manual",
        )
        assert out == "mimo | low"


# ---------------------------------------------------------------------------
# format_runtime_footer + build_footer_line
# ---------------------------------------------------------------------------


class TestFooterFieldStack:
    def test_route_source_passthrough(self):
        out = format_runtime_footer(
            model="mimo-v2.5",
            provider="opencode-go",
            reasoning_effort="low",
            route_label="mimo",
            context_tokens=0,
            context_length=None,
            cwd="",
            fields=("route_reasoning",),
            route_source="manual",
        )
        # The label doesn't change; route_source is plumbing for future
        # use and other consumers (footer log, trace).
        assert out == "mimo | low"

    def test_build_footer_opencode_go_omits_quota(self):
        """End-to-end: even when the gateway passes a non-None quota
        value, the Opencode Go footer must NOT include a % field.
        This is the no-Opencode-quota-indicator follow-up."""
        out = build_footer_line(
            user_config={"display": {"runtime_footer": {"enabled": True, "fields": ["route_reasoning"]}}},
            platform_key="telegram",
            model="mimo-v2.5",
            provider="opencode-go",
            reasoning_effort="low",
            route_label="mimo",
            codex_quota_used_percent=33.0,  # ignored — no opencode quota
            context_tokens=0,
            context_length=None,
            cwd="",
        )
        assert out == "mimo | low"
        assert "33" not in out
        assert "%" not in out

    def test_build_footer_opencode_zen_omits_quota(self):
        out = build_footer_line(
            user_config={"display": {"runtime_footer": {"enabled": True, "fields": ["route_reasoning"]}}},
            platform_key="telegram",
            model="gpt-5.4",
            provider="opencode",
            reasoning_effort="high",
            route_label="codex",
            codex_quota_used_percent=50.0,  # ignored — no opencode quota
            context_tokens=0,
            context_length=None,
            cwd="",
        )
        assert out == "codex | high"
        assert "50" not in out
        assert "%" not in out

    def test_build_footer_deepseek_omits_quota(self):
        out = build_footer_line(
            user_config={"display": {"runtime_footer": {"enabled": True, "fields": ["route_reasoning"]}}},
            platform_key="telegram",
            model="deepseek-v4-flash",
            provider="deepseek",
            reasoning_effort="low",
            route_label="deepseek-flash",
            codex_quota_used_percent=99.0,  # ignored — PAYG
            context_tokens=0,
            context_length=None,
            cwd="",
        )
        assert out == "deepseek-flash | low"

    def test_build_footer_codex_unchanged(self):
        out = build_footer_line(
            user_config={"display": {"runtime_footer": {"enabled": True, "fields": ["route_reasoning"]}}},
            platform_key="telegram",
            model="gpt-5.4-mini",
            provider="openai-codex",
            reasoning_effort="low",
            route_label="codex",
            codex_quota_used_percent=36.6,
            context_tokens=0,
            context_length=None,
            cwd="",
        )
        assert out == "codex | mini-low | 37%"
