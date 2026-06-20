"""Tests for the generic quota/account-snapshot service seam."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import gateway.quota_service as qs


@pytest.fixture(autouse=True)
def _reset_quota_service():
    """Reset the quota service registry between tests."""
    old_fetcher = qs._fetcher
    old_renderer = qs._renderer
    qs._fetcher = None
    qs._renderer = None
    yield
    qs._fetcher = old_fetcher
    qs._renderer = old_renderer


class TestQuotaServiceRegistry:
    def test_fetch_returns_none_when_no_fetcher(self):
        assert qs.fetch_quota_snapshot("openai-codex") is None

    def test_render_returns_empty_when_no_renderer(self):
        assert qs.render_quota_lines(object()) == []

    def test_render_returns_empty_when_falsy_snapshot(self):
        mock_renderer = MagicMock(return_value=["line"])
        qs.register_quota_renderer(mock_renderer)
        assert qs.render_quota_lines(None) == []
        mock_renderer.assert_not_called()

    def test_fetch_delegates_to_registered_fetcher(self):
        mock_snapshot = SimpleNamespace(provider="openai-codex")
        mock_fetcher = MagicMock(return_value=mock_snapshot)
        qs.register_quota_fetcher(mock_fetcher)

        result = qs.fetch_quota_snapshot("openai-codex", base_url="https://x")
        assert result is mock_snapshot
        mock_fetcher.assert_called_once_with("openai-codex", base_url="https://x")

    def test_render_delegates_to_registered_renderer(self):
        mock_renderer = MagicMock(return_value=["📈 Account limits", "Session: 50%"])
        qs.register_quota_renderer(mock_renderer)

        snapshot = SimpleNamespace(windows=())
        result = qs.render_quota_lines(snapshot, markdown=True)
        assert result == ["📈 Account limits", "Session: 50%"]
        mock_renderer.assert_called_once_with(snapshot, markdown=True)

    def test_fetch_returns_none_on_error(self):
        mock_fetcher = MagicMock(side_effect=RuntimeError("network"))
        qs.register_quota_fetcher(mock_fetcher)

        result = qs.fetch_quota_snapshot("openai-codex")
        assert result is None

    def test_render_returns_empty_on_error(self):
        mock_renderer = MagicMock(side_effect=RuntimeError("render"))
        qs.register_quota_renderer(mock_renderer)

        result = qs.render_quota_lines(SimpleNamespace(x=1))
        assert result == []

    def test_last_registration_wins(self):
        f1 = MagicMock(return_value="snap1")
        f2 = MagicMock(return_value="snap2")
        qs.register_quota_fetcher(f1)
        qs.register_quota_fetcher(f2)

        result = qs.fetch_quota_snapshot("p")
        assert result == "snap2"
        f2.assert_called_once()


class TestNoDirectPluginImportInGatewayRun:
    """Verify that gateway/run.py does NOT import plugins.account_usage directly."""

    def test_no_account_usage_import_in_source(self):
        import inspect
        import gateway.run as gateway_run_module

        source = inspect.getsource(gateway_run_module)
        # The source must not contain a direct import of the plugin module
        assert "from plugins.account_usage" not in source
        assert "import plugins.account_usage" not in source

    def test_backward_compat_shim_exists(self):
        """The shim functions must exist as module-level attributes."""
        import gateway.run as gw
        assert hasattr(gw, "fetch_account_usage")
        assert hasattr(gw, "render_account_usage_lines")
        assert callable(gw.fetch_account_usage)
        assert callable(gw.render_account_usage_lines)
