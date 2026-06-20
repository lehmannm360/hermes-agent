"""Tests for the gateway-noiseless-failover plugin policy tables."""
from __future__ import annotations

import importlib
import importlib.util
import sys
import types
from pathlib import Path

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


def _make_ctx(name="gateway-noiseless-failover"):
    manager = PluginManager()
    manager._discovered = True
    manifest = PluginManifest(
        name=name,
        path=str(_REPO_ROOT / "plugins" / "gateway-noiseless-failover"),
        source="bundled",
        kind="backend",
        key="gateway_noiseless_failover",
    )
    return PluginContext(manifest, manager)


class TestNoiselessFailoverPlugin:
    def test_manifest_exists(self):
        assert (_REPO_ROOT / "plugins" / "gateway-noiseless-failover" / "plugin.yaml").exists()

    def test_register_cli_command(self):
        mod = _load_plugin_module("gateway-noiseless-failover")
        ctx = _make_ctx()
        mod.register(ctx)
        manager = ctx._manager
        assert "noiseless-failover" in manager._cli_commands


class TestStatusPolicy:
    """Tests for should_suppress_status policy logic."""

    def _get_policy_fn(self):
        mod = _load_plugin_module("gateway-noiseless-failover")
        return mod.should_suppress_status

    def test_terminal_events_are_force_visible(self):
        should_suppress = self._get_policy_fn()
        result = should_suppress("anything", is_terminal=True)
        assert result["force_visible"] is True
        assert result["suppress"] is False
        assert result["reason"] == "terminal_event"

    def test_force_visible_kinds_are_visible(self):
        should_suppress = self._get_policy_fn()
        for kind in ("auth_failure", "billing_exhausted", "quota_exhausted",
                     "content_policy_blocked", "all_providers_failed",
                     "missing_fallback", "terminal_error", "fatal_error"):
            result = should_suppress(kind)
            assert result["force_visible"] is True, f"{kind} should be force_visible"
            assert result["suppress"] is False

    def test_silent_kinds_are_suppressed(self):
        should_suppress = self._get_policy_fn()
        for kind in ("stream_heartbeat", "provider_health_check"):
            result = should_suppress(kind)
            assert result["suppress"] is True
            assert result["silent"] is True

    def test_fallback_attempt_first_attempt_passes(self):
        should_suppress = self._get_policy_fn()
        result = should_suppress("fallback_attempt", attempt=1)
        assert result["suppress"] is False
        assert "first_attempt" in result["reason"]

    def test_fallback_attempt_later_suppressed(self):
        should_suppress = self._get_policy_fn()
        result = should_suppress("fallback_attempt", attempt=2)
        assert result["suppress"] is True
        assert "suppress_after_attempt" in result["reason"]

    def test_provider_retry_suppressed_after_first(self):
        should_suppress = self._get_policy_fn()
        result = should_suppress("provider_retry", attempt=3)
        assert result["suppress"] is True

    def test_stream_warming_first_attempt_visible(self):
        should_suppress = self._get_policy_fn()
        result = should_suppress("stream_warming", attempt=1)
        assert result["suppress"] is False

    def test_unknown_kinds_pass_through(self):
        should_suppress = self._get_policy_fn()
        result = should_suppress("some_new_kind", attempt=5)
        assert result["suppress"] is False
        assert result["force_visible"] is False
        assert result["reason"] == "default_pass"

    def test_force_visible_kinds_ignore_attempt(self):
        should_suppress = self._get_policy_fn()
        result = should_suppress("auth_failure", attempt=10)
        assert result["force_visible"] is True
        assert result["suppress"] is False
