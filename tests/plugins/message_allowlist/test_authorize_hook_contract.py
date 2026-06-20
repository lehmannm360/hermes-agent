"""Tests for the message-allowlist plugin authorization hook contract."""
from __future__ import annotations

import importlib
import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

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


def _make_ctx(name="message-allowlist"):
    manager = PluginManager()
    manager._discovered = True
    manifest = PluginManifest(
        name=name,
        path=str(_REPO_ROOT / "plugins" / "message-allowlist"),
        source="bundled",
        kind="backend",
        key="message_allowlist",
    )
    return PluginContext(manifest, manager)


def _make_source(user_id="123", platform_value="telegram", chat_type="dm"):
    platform = SimpleNamespace(value=platform_value)
    return SimpleNamespace(
        user_id=user_id,
        user_name="testuser",
        user_id_alt=None,
        chat_id="chat1",
        chat_id_alt=None,
        platform=platform,
        chat_type=chat_type,
    )


class TestMessageAllowlistPlugin:
    def test_manifest_exists(self):
        assert (_REPO_ROOT / "plugins" / "message-allowlist" / "plugin.yaml").exists()

    def test_register_registers_auth_hook(self):
        mod = _load_plugin_module("message-allowlist")
        ctx = _make_ctx()
        mod.register(ctx)
        manager = ctx._manager
        assert "pre_gateway_authorize_message" in manager._hooks

    def test_auth_hook_allows_when_not_enabled(self):
        mod = _load_plugin_module("message-allowlist")
        with patch("gateway.message_allowlist.message_allowlist_enabled", return_value=False):
            result = mod._pre_gateway_authorize_message_hook(source=_make_source())
        assert result == {"allow": True, "reason": "allowlist_not_enabled"}

    def test_auth_hook_allows_matching_member(self):
        mod = _load_plugin_module("message-allowlist")
        source = _make_source()
        with patch("gateway.message_allowlist.message_allowlist_enabled", return_value=True), \
             patch("gateway.message_allowlist.matching_message_allowlist_member", return_value={"member_id": "esa"}):
            result = mod._pre_gateway_authorize_message_hook(source=source)
        assert result is not None
        assert result["allow"] is True
        assert "esa" in result["reason"]

    def test_auth_hook_denies_non_matching_member(self):
        mod = _load_plugin_module("message-allowlist")
        source = _make_source()
        with patch("gateway.message_allowlist.message_allowlist_enabled", return_value=True), \
             patch("gateway.message_allowlist.matching_message_allowlist_member", return_value=None):
            result = mod._pre_gateway_authorize_message_hook(source=source)
        assert result is not None
        assert result.get("deny") is True

    def test_auth_hook_fails_closed_on_error(self):
        mod = _load_plugin_module("message-allowlist")
        source = _make_source()
        with patch("gateway.message_allowlist.message_allowlist_enabled", side_effect=RuntimeError("boom")):
            result = mod._pre_gateway_authorize_message_hook(source=source)
        assert result is not None
        assert result.get("deny") is True
        assert "hook_error" in result.get("reason", "")

    def test_auth_hook_with_no_source(self):
        mod = _load_plugin_module("message-allowlist")
        result = mod._pre_gateway_authorize_message_hook()
        assert result is None

    def test_register_cli_command(self):
        mod = _load_plugin_module("message-allowlist")
        ctx = _make_ctx()
        mod.register(ctx)
        manager = ctx._manager
        assert "message-allowlist" in manager._cli_commands
