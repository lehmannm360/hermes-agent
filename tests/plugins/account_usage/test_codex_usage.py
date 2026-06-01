from __future__ import annotations

from plugins.account_usage.usage import (
    AccountUsageSnapshot,
    AccountUsageWindow,
    fetch_account_usage,
    render_account_usage_lines,
)


class _Resp:
    def __init__(self, payload):
        self._payload = payload
        self.requested = None
    def raise_for_status(self):
        return None
    def json(self):
        return self._payload


class _Client:
    def __init__(self, payload, captured):
        self.payload = payload
        self.captured = captured
    def __enter__(self):
        return self
    def __exit__(self, exc_type, exc, tb):
        return False
    def get(self, url, headers=None):
        self.captured['url'] = url
        self.captured['headers'] = dict(headers or {})
        return _Resp(self.payload)


def test_fetch_account_usage_codex_pool_only_without_singleton_id(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        "plugins.account_usage.usage.resolve_codex_runtime_credentials",
        lambda refresh_if_expiring=True: {"api_key": "codex-token", "base_url": "https://chatgpt.com/backend-api/codex"},
    )
    monkeypatch.setattr("plugins.account_usage.usage._read_codex_tokens", lambda: {"tokens": {}})
    monkeypatch.setattr(
        "plugins.account_usage.usage.httpx.Client",
        lambda timeout=15.0: _Client({"rate_limit": {"primary_window": {"used_percent": 12.5, "reset_at": "2026-06-01T12:00:00Z"}, "secondary_window": {"used_percent": 55.0}}, "plan_type": "pro"}, captured),
    )
    snapshot = fetch_account_usage("openai-codex")
    assert snapshot is not None
    assert captured["headers"]["Authorization"] == "Bearer codex-token"
    assert "ChatGPT-Account-Id" not in captured["headers"]
    assert snapshot.plan == "Pro"
    assert any(w.label == "Session" for w in snapshot.windows)


def test_render_account_usage_lines_formats_windows():
    snapshot = AccountUsageSnapshot(
        provider="openai-codex",
        source="usage_api",
        fetched_at=__import__("datetime").datetime.now(__import__("datetime").timezone.utc),
        plan="Pro",
        windows=(
            AccountUsageWindow(label="Session", used_percent=25.0),
            AccountUsageWindow(label="Weekly", used_percent=72.0),
        ),
        details=("Credits balance: unlimited",),
    )
    lines = render_account_usage_lines(snapshot)
    assert lines[0].startswith("📈 ")
    assert "Provider: openai-codex (Pro)" in lines
    assert any("Session: 75% remaining (25% used)" in line for line in lines)
    assert any("Weekly: 28% remaining (72% used)" in line for line in lines)
    assert any("Credits balance: unlimited" in line for line in lines)
