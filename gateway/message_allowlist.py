"""Cross-platform message allowlist registry.

The registry is intentionally stored in the normal Hermes config file under
``security.message_allowlist`` so operators can keep one team/member list with
all channel account identifiers instead of duplicating per-platform env vars.

Example::

    security:
      message_allowlist:
        enabled: true
        members:
          esa:
            display_name: Esa
            role: owner
            permissions: [owner, chat, reminders, approvals]
            accounts:
              telegram:
                user_ids: ["637486142"]
              whatsapp:
                user_ids: ["60123456789@s.whatsapp.net"]
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from typing import Any

from hermes_constants import get_hermes_home

logger = logging.getLogger(__name__)


_ID_KEYS = ("user_ids", "usernames", "ids")
_CHAT_KEYS = ("chat_ids", "group_ids", "thread_ids")


def _load_config_yaml() -> dict[str, Any]:
    path = get_hermes_home() / "config.yaml"
    if not path.exists():
        return {}
    try:
        import yaml

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # pragma: no cover - defensive logging only
        logger.warning("Failed to load message allowlist from %s: %s", path, exc)
        return {}
    return data if isinstance(data, dict) else {}


def _message_allowlist_block() -> dict[str, Any]:
    data = _load_config_yaml()
    security = data.get("security", {})
    if not isinstance(security, Mapping):
        return {}
    block = security.get("message_allowlist", {})
    return dict(block) if isinstance(block, Mapping) else {}


def message_allowlist_enabled() -> bool:
    """Return True when the global message allowlist block is enabled."""
    block = _message_allowlist_block()
    return bool(block) and block.get("enabled", True) is not False


def message_allowlist_configured() -> bool:
    """Return True when a global message allowlist has enabled members."""
    if not message_allowlist_enabled():
        return False
    members = _message_allowlist_block().get("members", {})
    return isinstance(members, Mapping) and bool(members)


def _iter_values(value: Any) -> Iterable[str]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray, Mapping)):
        return (str(item) for item in value if item is not None)
    return (str(value),)


def _candidate_ids(source: Any) -> set[str]:
    candidates: set[str] = set()
    for attr in ("user_id", "user_id_alt", "chat_id", "chat_id_alt"):
        value = getattr(source, attr, None)
        if value:
            candidates.add(str(value).strip())
    user_name = getattr(source, "user_name", None)
    if user_name:
        raw = str(user_name).strip()
        candidates.add(raw)
        if raw and not raw.startswith("@"):
            candidates.add(f"@{raw}")

    # WhatsApp bridge IDs can appear as phone numbers, @s.whatsapp.net JIDs,
    # or @lid aliases.  Expand both configured IDs and incoming IDs using the
    # same normalizer used by the legacy WHATSAPP_ALLOWED_USERS path.
    try:
        platform_value = getattr(getattr(source, "platform", None), "value", "")
        if platform_value == "whatsapp":
            from gateway.run import _expand_whatsapp_auth_aliases  # local import avoids startup cycles

            expanded = set(candidates)
            for candidate in candidates:
                expanded.update(_expand_whatsapp_auth_aliases(candidate))
            candidates = expanded
    except Exception:
        pass

    return {c for c in candidates if c}


def _configured_ids_for_account(account: Mapping[str, Any]) -> set[str]:
    ids: set[str] = set()
    for key in (*_ID_KEYS, *_CHAT_KEYS):
        for value in _iter_values(account.get(key)):
            value = value.strip()
            if value:
                ids.add(value)
    return ids


def _member_enabled(member: Mapping[str, Any]) -> bool:
    return member.get("enabled", True) is not False and member.get("status", "active") != "disabled"


def matching_message_allowlist_member(source: Any) -> dict[str, Any] | None:
    """Return the configured member matching a SessionSource, if any.

    The returned dict includes ``member_id`` plus the configured member fields.
    Authorization only checks identity. Permissions are stored for future command
    policy layers and surfaced to operators, but not enforced here yet.
    """
    block = _message_allowlist_block()
    if not block or block.get("enabled", True) is False:
        return None

    platform_value = getattr(getattr(source, "platform", None), "value", None)
    if not platform_value:
        return None

    members = block.get("members", {})
    if not isinstance(members, Mapping):
        return None

    candidates = _candidate_ids(source)
    if not candidates:
        return None

    for member_id, raw_member in members.items():
        if not isinstance(raw_member, Mapping) or not _member_enabled(raw_member):
            continue
        accounts = raw_member.get("accounts", {})
        if not isinstance(accounts, Mapping):
            continue
        raw_account = accounts.get(platform_value)
        if not isinstance(raw_account, Mapping):
            continue
        configured_ids = _configured_ids_for_account(raw_account)

        if platform_value == "whatsapp":
            try:
                from gateway.run import _expand_whatsapp_auth_aliases

                expanded = set(configured_ids)
                for configured_id in configured_ids:
                    expanded.update(_expand_whatsapp_auth_aliases(configured_id))
                configured_ids = expanded
            except Exception:
                pass

        if candidates & configured_ids:
            member = dict(raw_member)
            member["member_id"] = str(member_id)
            return member
    return None


def message_allowlist_authorizes(source: Any) -> bool:
    """Return True if ``source`` matches an enabled member account."""
    return matching_message_allowlist_member(source) is not None
