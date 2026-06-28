"""Tests for ``/model auto`` — return the session to adaptive routing.

``/model auto`` replaces the previous ``/routing auto`` command.  It
must clear the per-session manual model lock, drop the session-scoped
model override, and evict the cached agent so the next turn re-runs
the adaptive-routing hook with no pin in place.  When no lock is set,
it must return a no-op confirmation.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent, MessageType
from gateway.session import SessionEntry, SessionSource, build_session_key


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_source() -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        user_id="u1",
        chat_id="c1",
        user_name="tester",
        chat_type="dm",
    )


def _make_event(text: str = "/model auto") -> MessageEvent:
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=_make_source(),
        message_id="m1",
    )


def _make_runner():
    """Build a minimal GatewayRunner with the surfaces
    ``_handle_model_auto_routing`` touches."""
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")}
    )
    adapter = MagicMock()
    adapter.send = AsyncMock()
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._voice_mode = {}
    runner.hooks = SimpleNamespace(emit=AsyncMock(), loaded_hooks=False)
    runner._session_model_overrides = {}
    runner._session_reasoning_overrides = {}
    runner._pending_model_notes = {}
    runner._background_tasks = set()

    session_key = build_session_key(_make_source())
    session_entry = SessionEntry(
        session_key=session_key,
        session_id="sess-1",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = session_entry
    runner.session_store._entries = {session_key: session_entry}
    runner._session_model_lock = {}
    runner._running_agents = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._session_db = None
    runner._agent_cache = {}
    runner._agent_cache_lock = None  # disables _evict_cached_agent lock path

    # Stub out the helpers _handle_model_auto_routing needs.
    runner._normalize_source_for_session_key = lambda source: source
    runner._session_key_for_source = lambda source: build_session_key(source)
    runner._evict_cached_agent = lambda sk: None
    return runner


def _seed_lock_and_override(runner, session_key: str, *, model: str, provider: str):
    """Seed a manual lock + a session model override as if the user
    had previously run ``/model <name>``."""
    runner._session_model_lock[session_key] = {
        "model": model,
        "provider": provider,
        "source": "user",
    }
    runner._session_model_overrides[session_key] = {
        "model": model,
        "provider": provider,
        "api_key": "***",
        "base_url": "",
        "api_mode": "chat_completions",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_model_auto_clears_lock_and_override():
    """/model auto must clear the per-session lock and the model
    override, so the next turn re-runs adaptive routing."""
    runner = _make_runner()
    session_key = build_session_key(_make_source())
    _seed_lock_and_override(
        runner, session_key, model="mimo-v2.5", provider="opencode-go"
    )

    result = await runner._handle_model_auto_routing(_make_event())

    assert session_key not in runner._session_model_lock
    assert session_key not in runner._session_model_overrides
    # The success message is the "auto_cleared" catalog entry (prefix
    # "Routing: 🔄 returned to auto ...").  We just check for the
    # marker emoji + key fragment to avoid coupling to the exact
    # translation.
    assert "auto" in result.lower() and "🔄" in result


@pytest.mark.asyncio
async def test_model_auto_noop_when_no_lock():
    """/model auto on a session that has no manual lock returns the
    noop confirmation rather than raising."""
    runner = _make_runner()

    result = await runner._handle_model_auto_routing(_make_event())

    assert "auto" in result.lower()
    # Noop message contains the "already on auto" / ⚡ marker.
    assert "⚡" in result


@pytest.mark.asyncio
async def test_model_auto_only_clears_own_session():
    """/model auto on one session must not touch the lock of a
    different session — the manual lock is per-session."""
    runner = _make_runner()
    other_source = SessionSource(
        platform=Platform.TELEGRAM,
        user_id="u2",
        chat_id="c2",
        user_name="other",
        chat_type="dm",
    )
    other_key = build_session_key(other_source)
    session_key = build_session_key(_make_source())

    _seed_lock_and_override(runner, session_key, model="mimo-v2.5", provider="opencode-go")
    runner._session_model_lock[other_key] = {
        "model": "gpt-5.5",
        "provider": "openai-codex",
        "source": "user",
    }
    runner._session_model_overrides[other_key] = {
        "model": "gpt-5.5",
        "provider": "openai-codex",
        "api_key": "***",
        "base_url": "",
        "api_mode": "codex_responses",
    }

    await runner._handle_model_auto_routing(_make_event())

    assert session_key not in runner._session_model_lock
    assert session_key not in runner._session_model_overrides
    # The other session's lock + override must be untouched.
    assert other_key in runner._session_model_lock
    assert other_key in runner._session_model_overrides
    assert runner._session_model_lock[other_key]["model"] == "gpt-5.5"


@pytest.mark.asyncio
async def test_model_auto_routes_through_handle_model_command():
    """The /model auto flow must be reached via the standard model
    command dispatcher.  Driving _handle_model_command with the
    'auto' arg must produce the same effect as the direct call."""
    runner = _make_runner()
    session_key = build_session_key(_make_source())
    _seed_lock_and_override(runner, session_key, model="mimo-v2.5", provider="opencode-go")

    # Stub out the heavy downstream of _handle_model_command so the
    # test focuses on the auto branch's effect, not the picker path.
    runner.adapters = {}  # no picker; falls through to direct switch
    # Bypass the cost guard / switch_model by returning a known
    # success result shape.
    async def _fake_to_thread(fn, *args, **kwargs):
        return SimpleNamespace(
            success=True,
            new_model="mimo-v2.5",
            target_provider="opencode-go",
            api_key="***",
            base_url="",
            api_mode="chat_completions",
            provider_label="OpenCode Go",
            model_info=None,
            warning_message="",
            error_message="",
        )

    runner._get_codex_quota_used_percent = lambda: None

    # Patch parse_model_flags so it returns "auto" as the model arg.
    from hermes_cli.model_switch import parse_model_flags as _real_parse
    import gateway.slash_commands as _sc

    def _fake_parse(raw_args):
        return ("auto", None, False, False, False)

    _sc.parse_model_flags = _fake_parse

    try:
        await runner._handle_model_command(_make_event("/model auto"))
    finally:
        _sc.parse_model_flags = _real_parse

    # /model auto cleared the lock + override.
    assert session_key not in runner._session_model_lock
    assert session_key not in runner._session_model_overrides


@pytest.mark.asyncio
async def test_picker_auto_sentinel_routes_to_auto_flow(monkeypatch):
    """Tapping the Auto/adaptive affordance in the interactive picker must
    route through the shared ``_on_model_selected`` closure to
    ``_handle_model_auto_routing`` — clearing the manual lock + override.
    This is the exact visible model-selection surface that lacked
    ``/model auto`` before the fix (the picker only showed provider/model
    buttons, never an Auto entry)."""
    runner = _make_runner()
    session_key = build_session_key(_make_source())
    _seed_lock_and_override(runner, session_key, model="mimo-v2.5", provider="opencode-go")

    # Fake picker-capable adapter that captures the on_model_selected closure.
    captured = {}

    class _FakePickerAdapter:
        async def send_model_picker(self, *, on_model_selected, **kwargs):
            captured["cb"] = on_model_selected
            return SimpleNamespace(success=True)

    runner.adapters = {Platform.TELEGRAM: _FakePickerAdapter()}

    # Stub the heavy bits the no-arg picker path pulls in.
    monkeypatch.setattr(
        "gateway.run._load_gateway_config",
        lambda: {"model": {"default": "mimo-v2.5", "provider": "opencode-go"}, "providers": {}},
    )
    monkeypatch.setattr(
        "hermes_cli.model_switch.list_picker_providers",
        lambda **kw: [{"slug": "opencode-go", "name": "OpenCode Go", "models": ["mimo-v2.5"], "total_models": 1, "is_current": True}],
    )

    # Bare /model → picker sent, closure captured.
    sent = await runner._handle_model_command(_make_event("/model"))
    assert sent is None
    assert "cb" in captured, "picker callback was not wired"

    # Simulate the user tapping the Auto/adaptive button.
    result = await captured["cb"]("c1", "auto", "__auto__")

    # The closure routed to _handle_model_auto_routing: lock + override cleared.
    assert session_key not in runner._session_model_lock
    assert session_key not in runner._session_model_overrides
    assert "auto" in result.lower()
    assert "🔄" in result
