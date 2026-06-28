"""Tests for the final-decision semantics added to
``_resolve_turn_agent_config``.

The plugin's ``resolve_turn_route`` hook may return
``final_decision=True`` to indicate that the gateway should apply the
plugin's decision directly and skip the core
``decide_turn_route()`` call.  This module exercises the seam without
booting the gateway — it patches the hook return value, calls the
real function, and asserts the resulting route.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Helper: build a minimal GatewayRunner-like object
# ---------------------------------------------------------------------------


class _StubRuntime:
    """Stand-in for a GatewayRunner that exposes only the surface
    ``_resolve_turn_agent_config`` needs."""

    def __init__(self):
        self._service_tier = None
        self._session_model_lock = {}

    def _runtime_dict_from_kwargs(self, runtime_kwargs):
        return {
            "api_key": runtime_kwargs.get("api_key"),
            "base_url": runtime_kwargs.get("base_url"),
            "provider": runtime_kwargs.get("provider"),
            "api_mode": runtime_kwargs.get("api_mode"),
            "command": runtime_kwargs.get("command"),
            "args": list(runtime_kwargs.get("args") or []),
            "credential_pool": runtime_kwargs.get("credential_pool"),
            "max_tokens": runtime_kwargs.get("max_tokens"),
        }

    def _route_signature(self, model, runtime):
        return (
            model,
            runtime.get("provider"),
            runtime.get("base_url"),
            runtime.get("api_mode"),
            runtime.get("command"),
            tuple(runtime.get("args") or []),
        )

    def _lookup_manual_lock(self, session_key):
        """Mirror the gateway's manual-lock lookup against the stub's
        ``_session_model_lock`` dict (the production method reads the same
        attribute via getattr)."""
        return self._session_model_lock.get(str(session_key or ""))

    def _load_reasoning_policy(self):
        # Disable core routing for the test by returning an empty policy
        # so the function only exercises the hook block, then either
        # proceeds to core (advisory case) or short-circuits (final
        # case) depending on the hook payload.
        return {"enabled": True, "stacks": {}}

    def _get_codex_quota_state(self):
        return None

    def _fallback_model(self):  # used as initial route key
        return None


def _bind_method(gateway_runner_cls):
    """Bind the real method to a stub instance for isolated testing."""
    return gateway_runner_cls._resolve_turn_agent_config


# ---------------------------------------------------------------------------
# Final hook semantics
# ---------------------------------------------------------------------------


class TestResolveTurnAgentConfigFinalHook:
    def test_final_decision_skips_core_routing(self):
        from gateway import run as gw_run
        method = _bind_method(gw_run.GatewayRunner)
        stub = _StubRuntime()
        stub._session_model_lock["telegram:12345"] = {
            "model": "mimo-v2.5",
            "provider": "opencode-go",
            "source": "user",
        }

        hook_payload = {
            "provider": "opencode-go",
            "model": "mimo-v2.5",
            "reasoning_effort": "low",
            "route_label": "mimo",
            "route_source": "adaptive",
            "final_decision": True,
        }

        with patch("hermes_cli.plugins.invoke_hook", return_value=[hook_payload]):
            with patch.object(gw_run, "_fetch_quota_snapshot", return_value=None):
                route = method(
                    stub,
                    user_message="hi",
                    model="mimo-v2.5",
                    runtime_kwargs={
                        "provider": "opencode-go",
                        "base_url": "https://example.invalid/v1",
                        "api_key": "x",
                        "api_mode": "chat_completions",
                    },
                    reasoning_config=None,
                    force_reasoning_config=False,
                    session_key="telegram:12345",
                )

        # The plugin's decision must be applied directly.  The
        # `route_hook_final` marker is the signal that core
        # ``decide_turn_route()`` was bypassed.
        assert route.get("route_hook_final") is True
        assert route["model"] == "mimo-v2.5"
        assert route["route_label"] == "mimo"
        assert route["route_source"] == "manual"
        # ``reasoning_config`` is set from the hook's reasoning_effort.
        assert route["reasoning_config"]["effort"] == "low"

    def test_session_key_is_passed_to_route_hook_for_manual_lock(self):
        from gateway import run as gw_run
        method = _bind_method(gw_run.GatewayRunner)
        stub = _StubRuntime()
        captured = {}

        def _hook(*args, **kwargs):
            captured.update(kwargs)
            return []

        with patch("hermes_cli.plugins.invoke_hook", side_effect=_hook):
            with patch.object(gw_run, "_fetch_quota_snapshot", return_value=None):
                method(
                    stub,
                    user_message="please use the manually selected model",
                    model="manual-model",
                    runtime_kwargs={
                        "provider": "manual-provider",
                        "base_url": "https://example.invalid/v1",
                        "api_key": "x",
                        "api_mode": "chat_completions",
                    },
                    reasoning_config=None,
                    force_reasoning_config=False,
                    session_key="telegram:12345",
                )

        assert captured["session_key"] == "telegram:12345"

    def test_advisory_hook_does_not_set_final_marker(self):
        from gateway import run as gw_run
        method = _bind_method(gw_run.GatewayRunner)
        stub = _StubRuntime()

        # Advisory: final_decision is missing or False.
        hook_payload = {
            "route_label": "mimo",
            "reasoning_effort": "low",
        }

        with patch("hermes_cli.plugins.invoke_hook", return_value=[hook_payload]):
            with patch.object(gw_run, "_fetch_quota_snapshot", return_value=None):
                route = method(
                    stub,
                    user_message="hi",
                    model="mimo-v2.5",
                    runtime_kwargs={
                        "provider": "opencode-go",
                        "base_url": "https://example.invalid/v1",
                        "api_key": "x",
                        "api_mode": "chat_completions",
                    },
                    reasoning_config=None,
                    force_reasoning_config=False,
                )

        # Without a final_decision, the gateway still calls core
        # ``decide_turn_route()`` and overwrites the model.
        assert route.get("route_hook_final") is None or not route.get("route_hook_final")
        # Core routing kept us on the primary model.
        assert route["model"] == "mimo-v2.5"

    def test_force_reasoning_config_bypasses_hook(self):
        from gateway import run as gw_run
        method = _bind_method(gw_run.GatewayRunner)
        stub = _StubRuntime()

        hook_payload = {
            "provider": "opencode-go",
            "model": "mimo-v2.5",
            "reasoning_effort": "low",
            "final_decision": True,
        }

        with patch("hermes_cli.plugins.invoke_hook", return_value=[hook_payload]) as mock_hook:
            with patch.object(gw_run, "_fetch_quota_snapshot", return_value=None):
                route = method(
                    stub,
                    user_message="hi",
                    model="mimo-v2.5",
                    runtime_kwargs={
                        "provider": "opencode-go",
                        "base_url": "https://example.invalid/v1",
                        "api_key": "x",
                        "api_mode": "chat_completions",
                    },
                    reasoning_config={"effort": "high"},
                    force_reasoning_config=True,
                )

        # Hook is gated behind force_reasoning_config=False, so it
        # shouldn't have been invoked at all.
        mock_hook.assert_not_called()
        # The forced reasoning config is preserved untouched.
        assert route["reasoning_config"] == {"effort": "high"}

    def test_hook_dangerous_keys_filtered(self):
        from gateway import run as gw_run
        method = _bind_method(gw_run.GatewayRunner)
        stub = _StubRuntime()

        hook_payload = {
            "provider": "opencode-go",
            "model": "mimo-v2.5",
            "messages": ["injected"],  # dangerous — must be filtered
            "system": "injected",      # dangerous — must be filtered
            "tools": ["injected"],     # dangerous — must be filtered
            "final_decision": True,
        }

        with patch("hermes_cli.plugins.invoke_hook", return_value=[hook_payload]):
            with patch.object(gw_run, "_fetch_quota_snapshot", return_value=None):
                route = method(
                    stub,
                    user_message="hi",
                    model="mimo-v2.5",
                    runtime_kwargs={
                        "provider": "opencode-go",
                        "base_url": "https://example.invalid/v1",
                        "api_key": "x",
                        "api_mode": "chat_completions",
                    },
                    reasoning_config=None,
                    force_reasoning_config=False,
                )

        # The provider/model were applied but the dangerous keys were
        # filtered — they must not appear on the route dict.
        for key in ("messages", "system", "tools", "history", "toolsets", "memory"):
            assert key not in route
        assert route["model"] == "mimo-v2.5"


# ---------------------------------------------------------------------------
# Manifest provider — no special adaptive-routing handling
# ---------------------------------------------------------------------------


class TestResolveTurnAgentConfigNoManifestSpecialCase:
    """The private-fork Manifest.build provider used to short-circuit
    adaptive routing by tagging ``route_label='manifest'`` and
    skipping the policy hook.  That custom branch is now removed
    (Manifest is not in the planned model stacks) so a Manifest
    base_url / provider name must flow through the same path as
    every other provider: the resolve_turn_route hook still fires,
    no manifest-only route_label is auto-set, and the resulting
    route carries whatever the hook returned."""

    def test_manifest_provider_does_not_get_route_label_manifest(self):
        from gateway import run as gw_run
        method = _bind_method(gw_run.GatewayRunner)
        stub = _StubRuntime()

        # Hook returns a normal "opencode-go" / mimo-v2.5 decision;
        # there is no special Manifest branch that would tag the
        # route with route_label="manifest".
        hook_payload = {
            "provider": "opencode-go",
            "model": "mimo-v2.5",
            "reasoning_effort": "low",
            "route_label": "mimo",
            "route_source": "adaptive",
            "final_decision": True,
        }

        with patch("hermes_cli.plugins.invoke_hook", return_value=[hook_payload]):
            with patch.object(gw_run, "_fetch_quota_snapshot", return_value=None):
                route = method(
                    stub,
                    user_message="hi",
                    model="mimo-v2.5",
                    runtime_kwargs={
                        "provider": "manifest",
                        "base_url": "https://api.manifest.build/v1",
                        "api_key": "x",
                        "api_mode": "chat_completions",
                    },
                    reasoning_config=None,
                    force_reasoning_config=False,
                )

        # The route does NOT carry the legacy "manifest" route_label
        # that the removed branch used to install unconditionally.
        assert route.get("route_label") != "manifest"
        # The hook's decision (route_label="mimo") flows through.
        assert route["route_label"] == "mimo"
        assert route["model"] == "mimo-v2.5"
        assert route["runtime"]["provider"] == "opencode-go"

    def test_manifest_build_base_url_does_not_get_route_label_manifest(self):
        """The removed branch also matched a base_url of
        ``manifest.build`` even with a different provider.  A
        legacy custom endpoint that happens to live on that host
        must NOT be auto-tagged route_label='manifest'."""
        from gateway import run as gw_run
        method = _bind_method(gw_run.GatewayRunner)
        stub = _StubRuntime()

        hook_payload = {
            "provider": "openai-codex",
            "model": "gpt-5.5",
            "reasoning_effort": "xhigh",
            "route_label": "codex",
            "route_source": "adaptive",
            "final_decision": True,
        }

        with patch("hermes_cli.plugins.invoke_hook", return_value=[hook_payload]):
            with patch.object(gw_run, "_fetch_quota_snapshot", return_value=None):
                route = method(
                    stub,
                    user_message="design the auth system",
                    model="gpt-5.5",
                    runtime_kwargs={
                        "provider": "openai-codex",
                        "base_url": "https://api.manifest.build/v1",
                        "api_key": "x",
                        "api_mode": "codex_responses",
                    },
                    reasoning_config=None,
                    force_reasoning_config=False,
                )

        assert route.get("route_label") != "manifest"
        assert route["route_label"] == "codex"
        assert route["model"] == "gpt-5.5"


# ---------------------------------------------------------------------------
# Manual model lock honored even when the adaptive-routing plugin is NOT
# loaded (kind: standalone is opt-in via plugins.enabled).  The gateway
# must not depend on the plugin's resolve_turn_route hook to respect an
# explicit /model <name> selection — core decide_turn_route() would
# otherwise overwrite it on the next turn.
# ---------------------------------------------------------------------------


class TestResolveTurnAgentConfigManualLockFallback:
    def test_manual_lock_wins_when_hook_returns_no_final_decision(self):
        """When the plugin is not enabled (hook returns []), a per-session
        manual model lock must still pin provider/model and skip core
        decide_turn_route().  This is the exact path that broke manual
        model selection before the fix."""
        from gateway import run as gw_run
        method = _bind_method(gw_run.GatewayRunner)
        stub = _StubRuntime()
        stub._session_model_lock["telegram:12345"] = {
            "model": "manual-model",
            "provider": "openai-codex",
            "source": "user",
        }

        # Plugin not loaded → hook returns no results.
        with patch("hermes_cli.plugins.invoke_hook", return_value=[]):
            with patch.object(gw_run, "_fetch_quota_snapshot", return_value=None):
                route = method(
                    stub,
                    user_message="anything — adaptive routing must not override",
                    model="manual-model",
                    runtime_kwargs={
                        "provider": "openai-codex",
                        "base_url": "https://example.invalid/v1",
                        "api_key": "x",
                        "api_mode": "codex_responses",
                    },
                    reasoning_config=None,
                    force_reasoning_config=False,
                    session_key="telegram:12345",
                )

        # The manual lock pinned the model; core routing was skipped.
        assert route["model"] == "manual-model"
        assert route["runtime"]["provider"] == "openai-codex"
        assert route.get("route_hook_final") is True
        assert route.get("route_source") == "manual"
        assert route.get("route_label") == "manual"

    def test_no_manual_lock_falls_through_to_core_routing(self):
        """Without a manual lock, the absence of a plugin final decision
        must still let core decide_turn_route() run (the fallback must
        not short-circuit normal adaptive routing)."""
        from gateway import run as gw_run
        method = _bind_method(gw_run.GatewayRunner)
        stub = _StubRuntime()
        # No lock installed.

        with patch("hermes_cli.plugins.invoke_hook", return_value=[]):
            with patch.object(gw_run, "_fetch_quota_snapshot", return_value=None):
                route = method(
                    stub,
                    user_message="hi",
                    model="mimo-v2.5",
                    runtime_kwargs={
                        "provider": "opencode-go",
                        "base_url": "https://example.invalid/v1",
                        "api_key": "x",
                        "api_mode": "chat_completions",
                    },
                    reasoning_config=None,
                    force_reasoning_config=False,
                    session_key="telegram:12345",
                )

        # No manual pin → route_hook_final stays unset, core routing ran
        # and kept the primary model (policy stacks empty → primary wins).
        assert not route.get("route_hook_final")
        assert route["model"] == "mimo-v2.5"
