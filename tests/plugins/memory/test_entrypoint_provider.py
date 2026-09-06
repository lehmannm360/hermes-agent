from types import SimpleNamespace

import plugins.memory as memory_plugins


class _Provider:
    name = "mnemosyne"

    def is_available(self):
        return True


class _Module:
    @staticmethod
    def register_memory_provider(ctx):
        ctx.register_memory_provider(_Provider())


class _EntryPoint:
    name = "mnemosyne"
    group = "hermes_agent.memory_providers"
    value = "fake_mnemosyne"
    dist = SimpleNamespace(metadata={"Summary": "Installed local memory provider"})

    def load(self):
        return _Module


def test_entrypoint_provider_is_discovered_without_user_plugin_directory(monkeypatch):
    entrypoint = _EntryPoint()
    monkeypatch.setattr(memory_plugins, "_iter_provider_dirs", lambda: [])
    monkeypatch.setattr(memory_plugins, "_memory_provider_entrypoints", lambda: [entrypoint])

    assert memory_plugins.list_memory_provider_names() == ["mnemosyne"]
    assert memory_plugins.discover_memory_providers() == [
        ("mnemosyne", "Installed local memory provider", True)
    ]

    provider = memory_plugins.load_memory_provider("mnemosyne")
    assert provider is not None
    assert provider.name == "mnemosyne"
    assert provider.is_available() is True
