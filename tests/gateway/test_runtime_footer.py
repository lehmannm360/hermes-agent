"""Unit tests for gateway.runtime_footer — the opt-in runtime-metadata footer
appended to final gateway replies."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from gateway.runtime_footer import (
    _codex_quota_used_percent_from_snapshot,
    _home_relative_cwd,
    _model_short,
    build_footer_line,
    format_runtime_footer,
    resolve_footer_config,
)


# ---------------------------------------------------------------------------
# _model_short + _home_relative_cwd
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "model,expected",
    [
        ("openai/gpt-5.4", "gpt-5.4"),
        ("anthropic/claude-sonnet-4.6", "claude-sonnet-4.6"),
        ("gpt-5.4", "gpt-5.4"),
        ("", ""),
        (None, ""),
    ],
)
def test_model_short_drops_vendor_prefix(model, expected):
    assert _model_short(model) == expected


def test_home_relative_cwd_collapses_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    sub = tmp_path / "projects" / "hermes"
    sub.mkdir(parents=True)
    result = _home_relative_cwd(str(sub))
    assert result == "~/projects/hermes"


def test_home_relative_cwd_leaves_abs_path_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "other"))
    result = _home_relative_cwd(str(tmp_path / "outside" / "dir"))
    assert result == str(tmp_path / "outside" / "dir")


def test_home_relative_cwd_empty_returns_empty():
    assert _home_relative_cwd("") == ""


# ---------------------------------------------------------------------------
# format_runtime_footer
# ---------------------------------------------------------------------------

def test_format_footer_all_fields(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path / "projects" / "hermes"))
    (tmp_path / "projects" / "hermes").mkdir(parents=True)
    out = format_runtime_footer(
        model="openrouter/openai/gpt-5.4",
        context_tokens=68000,
        context_length=100000,
        cwd=None,  # falls back to TERMINAL_CWD env var
        fields=("model", "context_pct", "cwd"),
    )
    assert out == "gpt-5.4 · 68% · ~/projects/hermes"


def test_format_footer_skips_missing_context_length():
    out = format_runtime_footer(
        model="openai/gpt-5.4",
        context_tokens=500,
        context_length=None,
        cwd="/tmp/wd",
        fields=("model", "context_pct", "cwd"),
    )
    # context_pct dropped silently; no "?%" artifact
    assert "%" not in out
    assert "gpt-5.4" in out
    assert "/tmp/wd" in out


def test_format_footer_context_pct_clamped_to_100():
    out = format_runtime_footer(
        model="m",
        context_tokens=500_000,  # way over
        context_length=100_000,
        cwd="",
        fields=("context_pct",),
    )
    assert out == "100%"


def test_format_footer_context_pct_never_negative():
    out = format_runtime_footer(
        model="m",
        context_tokens=-50,
        context_length=100,
        cwd="",
        fields=("context_pct",),
    )
    # Negative input => no field emitted (we require context_tokens >= 0)
    assert out == ""


def test_format_footer_empty_fields_returns_empty():
    out = format_runtime_footer(
        model="m", context_tokens=0, context_length=100,
        cwd="/x", fields=(),
    )
    assert out == ""


def test_format_footer_drops_cwd_when_empty(monkeypatch):
    monkeypatch.delenv("TERMINAL_CWD", raising=False)
    out = format_runtime_footer(
        model="openai/gpt-5.4",
        context_tokens=50, context_length=100,
        cwd="",
        fields=("model", "context_pct", "cwd"),
    )
    # cwd silently dropped; model + pct remain
    assert out == "gpt-5.4 · 50%"


def test_format_footer_custom_field_order():
    out = format_runtime_footer(
        model="openai/gpt-5.4",
        context_tokens=50, context_length=100,
        cwd="/opt/project",
        fields=("context_pct", "model"),  # swapped + no cwd
    )
    assert out == "50% · gpt-5.4"


def test_format_footer_unknown_field_silently_ignored():
    out = format_runtime_footer(
        model="openai/gpt-5.4",
        context_tokens=50, context_length=100,
        cwd="/x",
        fields=("model", "bogus", "context_pct"),
    )
    assert out == "gpt-5.4 · 50%"


def test_format_footer_route_reasoning_field_uses_compact_pipe_separator():
    out = format_runtime_footer(
        model="openai/gpt-5.5",
        provider="openai-codex",
        reasoning_effort="xhigh",
        route_label="codex",
        context_tokens=0,
        context_length=None,
        cwd="",
        fields=("route_reasoning",),
    )
    assert out == "codex | xhigh"


def test_format_footer_codex_route_reasoning_includes_five_hour_quota_used_percent():
    out = format_runtime_footer(
        model="gpt-5.4-mini",
        provider="openai-codex",
        reasoning_effort="low",
        route_label="codex",
        codex_quota_used_percent=70.2,
        context_tokens=0,
        context_length=None,
        cwd="",
        fields=("route_reasoning",),
    )
    assert out == "codex | mini-low | 70%"


def test_format_footer_deepseek_route_reasoning_omits_cost_highlight():
    out = format_runtime_footer(
        model="deepseek-v4-flash",
        provider="deepseek",
        reasoning_effort="low",
        context_tokens=0,
        context_length=None,
        cwd="",
        fields=("route_reasoning",),
    )
    assert out == "deepseek-v4-flash | low"


def test_codex_quota_used_percent_from_snapshot_prefers_session_window():
    snapshot = SimpleNamespace(
        windows=(
            SimpleNamespace(label="Weekly", used_percent=12.0),
            SimpleNamespace(label="Session", used_percent=70.2),
        )
    )
    assert _codex_quota_used_percent_from_snapshot(snapshot) == 70.2


# ---------------------------------------------------------------------------
# resolve_footer_config
# ---------------------------------------------------------------------------

def test_resolve_defaults_off_empty_config():
    cfg = resolve_footer_config({}, "telegram")
    assert cfg == {"enabled": False, "fields": ["model", "context_pct", "cwd"], "style": "plain"}


def test_resolve_global_enable():
    user = {"display": {"runtime_footer": {"enabled": True}}}
    cfg = resolve_footer_config(user, "telegram")
    assert cfg["enabled"] is True
    assert cfg["fields"] == ["model", "context_pct", "cwd"]


def test_resolve_platform_override_wins():
    user = {
        "display": {
            "runtime_footer": {"enabled": True, "fields": ["model"]},
            "platforms": {
                "slack": {"runtime_footer": {"enabled": False}},
            },
        },
    }
    # Telegram picks up the global enable
    assert resolve_footer_config(user, "telegram")["enabled"] is True
    # Slack overrides to off
    assert resolve_footer_config(user, "slack")["enabled"] is False


def test_resolve_platform_can_add_fields_only():
    user = {
        "display": {
            "runtime_footer": {"enabled": True},
            "platforms": {
                "discord": {"runtime_footer": {"fields": ["context_pct"]}},
            },
        },
    }
    tg = resolve_footer_config(user, "telegram")
    assert tg["enabled"] is True
    assert tg["fields"] == ["model", "context_pct", "cwd"]
    dc = resolve_footer_config(user, "discord")
    assert dc["enabled"] is True
    assert dc["fields"] == ["context_pct"]


def test_resolve_ignores_malformed_config():
    # Non-dict runtime_footer shouldn't crash
    user = {"display": {"runtime_footer": "on"}}
    cfg = resolve_footer_config(user, "telegram")
    assert cfg["enabled"] is False


# ---------------------------------------------------------------------------
# build_footer_line — top-level entry point used by gateway/run.py
# ---------------------------------------------------------------------------

def test_build_footer_empty_when_disabled():
    out = build_footer_line(
        user_config={},
        platform_key="telegram",
        model="openai/gpt-5.4",
        context_tokens=10, context_length=100,
        cwd="/tmp",
    )
    assert out == ""


def test_build_footer_returns_rendered_when_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    out = build_footer_line(
        user_config={"display": {"runtime_footer": {"enabled": True}}},
        platform_key="telegram",
        model="openai/gpt-5.4",
        context_tokens=25, context_length=100,
        cwd=str(tmp_path / "proj"),
    )
    (tmp_path / "proj").mkdir(exist_ok=True)
    assert "gpt-5.4" in out
    assert "25%" in out


def test_build_footer_per_platform_off_suppresses():
    user = {
        "display": {
            "runtime_footer": {"enabled": True},
            "platforms": {"slack": {"runtime_footer": {"enabled": False}}},
        },
    }
    out = build_footer_line(
        user_config=user,
        platform_key="slack",
        model="openai/gpt-5.4",
        context_tokens=10, context_length=100,
        cwd="/tmp",
    )
    assert out == ""


def test_build_footer_route_reasoning_for_model_reasoning_footnote():
    out = build_footer_line(
        user_config={
            "display": {
                "runtime_footer": {
                    "enabled": True,
                    "fields": ["route_reasoning"],
                }
            }
        },
        platform_key="telegram",
        model="gpt-5.5",
        provider="openai-codex",
        reasoning_effort="xhigh",
        route_label="codex",
        context_tokens=0,
        context_length=None,
        cwd="",
    )
    assert out == "codex | xhigh"


def test_build_footer_response_ref_field_and_italic_style():
    out = build_footer_line(
        user_config={
            "display": {
                "runtime_footer": {
                    "enabled": True,
                    "fields": ["route_reasoning", "response_ref"],
                    "style": "italic",
                }
            }
        },
        platform_key="telegram",
        model="openai/gpt-5.4-mini",
        provider="openai-codex",
        reasoning_effort="low",
        route_label="codex",
        codex_quota_used_percent=36.6,
        response_ref="r-8f3a21c4",
        context_tokens=0,
        context_length=None,
        cwd="",
    )
    assert out == "*codex | mini-low | 37% · r-8f3a21c4*"


def test_build_footer_no_data_returns_empty_even_when_enabled():
    # Enabled, but context_length is None AND cwd empty AND model empty ⇒ no fields
    out = build_footer_line(
        user_config={"display": {"runtime_footer": {"enabled": True}}},
        platform_key="telegram",
        model="",
        context_tokens=0, context_length=None,
        cwd="",
    )
    # With no TERMINAL_CWD env either
    if not os.environ.get("TERMINAL_CWD"):
        assert out == ""


def test_format_footer_model_fallback_when_route_reasoning_empty():
    """When route_reasoning is in fields but has no data (no reasoning_effort),
    model should appear as a standalone fallback."""
    out = format_runtime_footer(
        model="openai/gpt-5.4",
        provider="manifest",
        reasoning_effort=None,  # not configured → route_reasoning returns ""
        context_tokens=0,
        context_length=None,
        cwd="",
        fields=("model", "route_reasoning", "response_ref"),
        response_ref="r-abc123",
    )
    # model appears as fallback, route_reasoning skipped, response_ref appended
    assert out == "gpt-5.4 · r-abc123"


def test_format_footer_no_duplicate_model_when_route_reasoning_works():
    """When route_reasoning produces output, model should NOT appear separately."""
    out = format_runtime_footer(
        model="gpt-5.5",
        provider="openai-codex",
        reasoning_effort="xhigh",
        route_label="codex",
        context_tokens=0,
        context_length=None,
        cwd="",
        fields=("model", "route_reasoning", "response_ref"),
        response_ref="r-abc123",
    )
    # "model" field is skipped when route_reasoning produces output
    assert out == "codex | xhigh · r-abc123"
