from __future__ import annotations

from pathlib import Path

from hermes_cli.plugins import PluginManager


def test_account_usage_plugin_manifest_exists():
    assert Path(__file__).resolve().parents[3].joinpath("plugins/account_usage/plugin.yaml").exists()


def test_account_usage_plugin_registers_cli_command(monkeypatch):
    from hermes_cli.plugins import PluginContext, PluginManifest
    from plugins.account_usage.usage import register_plugin

    manager = PluginManager()
    manager._discovered = True
    manifest = PluginManifest(name="account-usage", path=str(Path(__file__).resolve().parents[3] / "plugins/account_usage"), source="bundled", kind="backend", key="account_usage")
    ctx = PluginContext(manifest, manager)
    register_plugin(ctx)
    assert "account-usage" in manager._cli_commands or "account_usage" in manager._cli_commands
