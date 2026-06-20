"""Tests for the gateway-runtime-metadata plugin hook contract."""
from __future__ import annotations

import importlib
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from hermes_cli.plugins import PluginManager, PluginContext, PluginManifest

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_plugin_module(plugin_dir_name: str):
    """Load a plugin module from a hyphenated directory name."""
    slug = plugin_dir_name.replace("-", "_")
    module_name = f"hermes_plugins.{slug}"
    if module_name in sys.modules:
        return sys.modules[module_name]
    plugin_dir = _REPO_ROOT / "plugins" / plugin_dir_name
    init_file = plugin_dir / "__init__.py"
    ns_parent = "hermes_plugins"
    if ns_parent not in sys.modules:
        ns_pkg = types.ModuleType(ns_parent)
        ns_pkg.__path__ = []  # type: ignore[attr-defined]
        ns_pkg.__package__ = ns_parent
        sys.modules[ns_parent] = ns_pkg
    spec = importlib.util.spec_from_file_location(
        module_name,
        init_file,
        submodule_search_locations=[str(plugin_dir)],
    )
    module = importlib.util.module_from_spec(spec)
    module.__package__ = module_name
    module.__path__ = [str(plugin_dir)]
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _make_ctx(name="gateway-runtime-metadata"):
    manager = PluginManager()
    manager._discovered = True
    manifest = PluginManifest(
        name=name,
        path=str(_REPO_ROOT / "plugins" / "gateway-runtime-metadata"),
        source="bundled",
        kind="backend",
        key="gateway_runtime_metadata",
    )
    return PluginContext(manifest, manager)


class TestGatewayRuntimeMetadataPlugin:
    def test_manifest_exists(self):
        assert (_REPO_ROOT / "plugins" / "gateway-runtime-metadata" / "plugin.yaml").exists()

    def test_register_registers_hooks(self):
        mod = _load_plugin_module("gateway-runtime-metadata")
        ctx = _make_ctx()
        mod.register(ctx)
        manager = ctx._manager
        assert "format_gateway_runtime_footer" in manager._hooks
        assert "on_final_response_persisted" in manager._hooks

    def test_footer_hook_returns_none_by_default(self):
        mod = _load_plugin_module("gateway-runtime-metadata")
        result = mod._format_gateway_runtime_footer_hook(
            model="gpt-5.4",
            context_tokens=50000,
            context_length=200000,
        )
        assert result is None

    def test_persisted_hook_does_not_raise(self):
        mod = _load_plugin_module("gateway-runtime-metadata")
        mod._on_final_response_persisted_hook()
        mod._on_final_response_persisted_hook(ref_id="r-abc123", session_id="s1")

    def test_register_cli_command(self):
        mod = _load_plugin_module("gateway-runtime-metadata")
        ctx = _make_ctx()
        mod.register(ctx)
        manager = ctx._manager
        assert "response-ref" in manager._cli_commands
