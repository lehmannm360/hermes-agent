"""Tests for new plugin hook declarations and has_hook helper."""
from __future__ import annotations

import pytest

from hermes_cli.plugins import (
    VALID_HOOKS,
    PluginManager,
    PluginContext,
    PluginManifest,
    has_hook,
)


class TestHookDeclarations:
    """Verify all required hook names are declared in VALID_HOOKS."""

    def test_pre_gateway_authorize_message_in_valid_hooks(self):
        assert "pre_gateway_authorize_message" in VALID_HOOKS

    def test_format_gateway_runtime_footer_in_valid_hooks(self):
        assert "format_gateway_runtime_footer" in VALID_HOOKS

    def test_on_final_response_persisted_in_valid_hooks(self):
        assert "on_final_response_persisted" in VALID_HOOKS

    def test_resolve_turn_route_in_valid_hooks(self):
        assert "resolve_turn_route" in VALID_HOOKS

    def test_transform_status_event_in_valid_hooks(self):
        assert "transform_status_event" in VALID_HOOKS

    def test_existing_hooks_preserved(self):
        """Existing hooks must not be removed."""
        for hook in (
            "pre_tool_call", "post_tool_call", "transform_llm_output",
            "pre_llm_call", "post_llm_call", "on_session_start",
            "on_session_end", "pre_gateway_dispatch",
            "pre_approval_request", "post_approval_response",
        ):
            assert hook in VALID_HOOKS, f"{hook} removed from VALID_HOOKS"


class TestHasHook:
    """Tests for the has_hook helper method."""

    def test_has_hook_returns_false_when_empty(self):
        manager = PluginManager()
        manager._discovered = True
        assert manager.has_hook("nonexistent_hook") is False

    def test_has_hook_returns_true_when_registered(self):
        manager = PluginManager()
        manager._discovered = True
        manager._hooks["test_hook"] = [lambda: None]
        assert manager.has_hook("test_hook") is True

    def test_has_hook_returns_false_for_wrong_name(self):
        manager = PluginManager()
        manager._discovered = True
        manager._hooks["test_hook"] = [lambda: None]
        assert manager.has_hook("other_hook") is False

    def test_has_hook_empty_list_returns_false(self):
        manager = PluginManager()
        manager._discovered = True
        manager._hooks["test_hook"] = []
        assert manager.has_hook("test_hook") is False

    def test_module_level_has_hook_delegates(self):
        """The module-level has_hook() convenience function works."""
        # This tests the wrapper without needing a real plugin
        result = has_hook("pre_gateway_authorize_message")
        # Result depends on whether a plugin has registered a callback
        assert isinstance(result, bool)

    def test_has_hook_does_not_execute_callbacks(self):
        """has_hook must not call any callbacks."""
        called = []
        manager = PluginManager()
        manager._discovered = True
        manager._hooks["test_hook"] = [lambda: called.append(True)]
        assert manager.has_hook("test_hook") is True
        assert len(called) == 0  # callback was NOT called
