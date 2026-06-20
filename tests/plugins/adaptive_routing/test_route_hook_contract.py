"""Tests for the adaptive-routing plugin hook contract."""
from __future__ import annotations

import importlib
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from hermes_cli.plugins import PluginManager, PluginContext, PluginManifest

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_plugin_module(plugin_dir_name: str):
    slug = plugin_dir_name.replace("-", "_")
    module_name = f"hermes_plugins.{slug}"
    if module_name in sys.modules:
        return sys.modules[module_name]
    plugin_dir = _REPO_ROOT / "plugins" / plugin_dir_name
    init_file = plugin_dir / "__init__.py"
    ns_parent = "hermes_plugins"
    if ns_parent not in sys.modules:
        ns_pkg = types.ModuleType(ns_parent)
        ns_pkg.__path__ = []
        ns_pkg.__package__ = ns_parent
        sys.modules[ns_parent] = ns_pkg
    spec = importlib.util.spec_from_file_location(
        module_name, init_file,
        submodule_search_locations=[str(plugin_dir)],
    )
    module = importlib.util.module_from_spec(spec)
    module.__package__ = module_name
    module.__path__ = [str(plugin_dir)]
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _make_ctx(name="adaptive-routing"):
    manager = PluginManager()
    manager._discovered = True
    manifest = PluginManifest(
        name=name,
        path=str(_REPO_ROOT / "plugins" / "adaptive-routing"),
        source="bundled",
        kind="backend",
        key="adaptive_routing",
    )
    return PluginContext(manifest, manager)


class TestAdaptiveRoutingPlugin:
    def test_manifest_exists(self):
        assert (_REPO_ROOT / "plugins" / "adaptive-routing" / "plugin.yaml").exists()

    def test_register_registers_route_hook(self):
        mod = _load_plugin_module("adaptive-routing")
        ctx = _make_ctx()
        mod.register(ctx)
        manager = ctx._manager
        assert "resolve_turn_route" in manager._hooks

    def test_route_hook_defers_when_policy_disabled(self):
        mod = _load_plugin_module("adaptive-routing")
        result = mod._resolve_turn_route_hook(
            user_message="hello",
            primary_provider="openai-codex",
            primary_model="gpt-5.5",
            session_key="sk",
            policy={"enabled": False},
        )
        assert result is None

    def test_route_hook_defers_when_no_policy(self):
        mod = _load_plugin_module("adaptive-routing")
        result = mod._resolve_turn_route_hook(
            user_message="hello",
            policy={},
        )
        assert result is None

    def test_route_hook_returns_decision_when_enabled(self):
        mod = _load_plugin_module("adaptive-routing")
        from agent.reasoning_policy import DEFAULT_REASONING_POLICY

        result = mod._resolve_turn_route_hook(
            user_message="implement a new feature for the gateway module",
            primary_provider="openai-codex",
            primary_model="gpt-5.5",
            session_key="sk",
            policy={**DEFAULT_REASONING_POLICY, "enabled": True},
        )
        assert result is not None
        assert "provider" in result
        assert "model" in result
        assert "reasoning_effort" in result
        assert "route_label" in result

    def test_route_hook_does_not_return_dangerous_keys(self):
        mod = _load_plugin_module("adaptive-routing")
        from agent.reasoning_policy import DEFAULT_REASONING_POLICY

        result = mod._resolve_turn_route_hook(
            user_message="fix the authentication bug in the login flow",
            primary_provider="openai-codex",
            primary_model="gpt-5.5",
            session_key="sk",
            policy={**DEFAULT_REASONING_POLICY, "enabled": True},
        )
        if result is not None:
            dangerous = {"messages", "history", "tools", "toolsets", "system", "memory"}
            assert not (set(result.keys()) & dangerous)

    def test_route_hook_handles_classify_error(self):
        mod = _load_plugin_module("adaptive-routing")
        with patch("agent.reasoning_policy.decide_turn_route", side_effect=RuntimeError("boom")):
            result = mod._resolve_turn_route_hook(
                user_message="hello",
                primary_provider="openai-codex",
                primary_model="gpt-5.5",
                session_key="sk",
                policy={"enabled": True},
            )
        assert result is None

    def test_register_cli_command(self):
        mod = _load_plugin_module("adaptive-routing")
        ctx = _make_ctx()
        mod.register(ctx)
        manager = ctx._manager
        assert "adaptive-routing" in manager._cli_commands or "adaptive_routing" in manager._cli_commands
