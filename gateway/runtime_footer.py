"""Gateway runtime-metadata footer.

Renders a compact footer showing runtime state (model, context %, cwd) and
appends it to the FINAL message of an agent turn when enabled.  Off by default
to keep replies minimal.

Config (``~/.hermes/config.yaml``)::

    display:
      runtime_footer:
        enabled: true                       # off by default
        fields: [model, context_pct, cwd]   # or [route_reasoning] for `codex | xhigh`

Per-platform overrides live under ``display.platforms.<platform>.runtime_footer``.
Users can toggle the global setting with ``/footer on|off`` from both the CLI
and any gateway platform.

The footer is appended to the final response text in ``gateway/run.py`` right
before returning the response to the adapter send path — so it only lands on
the final message a user sees, not on tool-progress updates or streaming
partials.  When streaming is on and the final text has already been delivered
piecemeal, the footer is sent as a separate trailing message via
``send_trailing_footer()``.
"""

from __future__ import annotations

import math
import os
from typing import Any, Iterable, Optional

_DEFAULT_FIELDS: tuple[str, ...] = ("model", "context_pct", "cwd")
_SEP = " · "


def _home_relative_cwd(cwd: str) -> str:
    """Return *cwd* with ``$HOME`` collapsed to ``~``.  Empty string if unset."""
    if not cwd:
        return ""
    try:
        home = os.path.expanduser("~")
        p = os.path.abspath(cwd)
        if home and (p == home or p.startswith(home + os.sep)):
            return "~" + p[len(home):]
        return p
    except Exception:
        return cwd


def _model_short(model: Optional[str]) -> str:
    """Drop ``vendor/`` prefix for readability (``openai/gpt-5.4`` → ``gpt-5.4``)."""
    if not model:
        return ""
    return model.rsplit("/", 1)[-1]


def resolve_footer_config(
    user_config: dict[str, Any] | None,
    platform_key: str | None = None,
) -> dict[str, Any]:
    """Resolve effective runtime-footer config for *platform_key*.

    Merge order (later wins):
        1. Built-in defaults (enabled=False)
        2. ``display.runtime_footer``
        3. ``display.platforms.<platform_key>.runtime_footer``
    """
    resolved = {"enabled": False, "fields": list(_DEFAULT_FIELDS), "style": "plain"}
    cfg = (user_config or {}).get("display") or {}

    global_cfg = cfg.get("runtime_footer")
    if isinstance(global_cfg, dict):
        if "enabled" in global_cfg:
            resolved["enabled"] = bool(global_cfg.get("enabled"))
        if isinstance(global_cfg.get("fields"), list) and global_cfg["fields"]:
            resolved["fields"] = [str(f) for f in global_cfg["fields"]]
        if global_cfg.get("style"):
            resolved["style"] = str(global_cfg.get("style"))

    if platform_key:
        platforms = cfg.get("platforms") or {}
        plat_cfg = platforms.get(platform_key)
        if isinstance(plat_cfg, dict):
            plat_footer = plat_cfg.get("runtime_footer")
            if isinstance(plat_footer, dict):
                if "enabled" in plat_footer:
                    resolved["enabled"] = bool(plat_footer.get("enabled"))
                if isinstance(plat_footer.get("fields"), list) and plat_footer["fields"]:
                    resolved["fields"] = [str(f) for f in plat_footer["fields"]]
                if plat_footer.get("style"):
                    resolved["style"] = str(plat_footer.get("style"))

    return resolved


def _format_used_percent(value: Any) -> str:
    """Format a quota used percentage for the compact route footer."""
    try:
        pct = float(value)
    except (TypeError, ValueError):
        return ""
    if not math.isfinite(pct):
        return ""
    pct = max(0, min(100, round(pct)))
    return f"{pct}%"


def _codex_quota_used_percent_from_snapshot(snapshot: Any) -> Optional[float]:
    """Extract the Codex 5-hour/session used percentage from an account snapshot.

    Codex's usage API labels the 5-hour window as ``Session`` today.  Prefer
    session/five-hour-looking labels, but fall back to the first populated
    window so minor upstream label changes don't make the footer disappear.
    """
    windows = tuple(getattr(snapshot, "windows", ()) or ())
    first_populated: Optional[float] = None
    for window in windows:
        raw_used = getattr(window, "used_percent", None)
        if raw_used is None:
            continue
        try:
            used = float(raw_used)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(used):
            continue
        if first_populated is None:
            first_populated = used
        label = str(getattr(window, "label", "") or "").strip().lower()
        if "session" in label or "5" in label or "five" in label:
            return used
    return first_populated


def _route_reasoning_label(
    *,
    provider: Optional[str],
    model: Optional[str],
    reasoning_effort: Optional[str],
    route_label: Optional[str],
    codex_quota_used_percent: Any = None,
) -> str:
    effort = str(reasoning_effort or "").strip().lower()
    if not effort:
        return ""
    label = str(route_label or "").strip()
    provider_norm = str(provider or "").strip().lower()
    # Handle manifest route_label: use model name with m- prefix
    if route_label == "manifest":
        label = f"m-{_model_short(model)}" if _model_short(model) else "m-manifest"
    elif not label:
        if provider_norm in {"openai-codex", "codex"}:
            label = "codex"
        elif provider_norm == "deepseek":
            label = _model_short(model) or "deepseek"
        else:
            label = _model_short(model) or provider_norm
    if not label:
        return ""
    effort_label = effort
    if provider_norm in {"openai-codex", "codex"} and "mini" in _model_short(model).lower():
        effort_label = f"mini-{effort}"
    parts = [label, effort_label]
    if provider_norm in {"openai-codex", "codex"}:
        usage = _format_used_percent(codex_quota_used_percent)
        if usage:
            parts.append(usage)
    return " | ".join(parts)


def format_runtime_footer(
    *,
    model: Optional[str],
    context_tokens: int,
    context_length: Optional[int],
    cwd: Optional[str] = None,
    fields: Iterable[str] = _DEFAULT_FIELDS,
    provider: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    route_label: Optional[str] = None,
    codex_quota_used_percent: Any = None,
    response_ref: Optional[str] = None,
) -> str:
    """Render the footer line, or return "" if no fields have data.

    Fields are skipped silently when their underlying data is missing — a
    partially-populated footer is better than a line with ``?%`` or empty slots.
    """
    parts: list[str] = []
    _route_reasoning_produced = False
    for field in fields:
        if field == "model":
            # Defer to route_reasoning which embeds the model name.
            # If route_reasoning isn't in fields at all, show model directly.
            if "route_reasoning" not in fields:
                short = _model_short(model)
                if short:
                    parts.append(short)
        elif field == "context_pct":
            if context_length and context_length > 0 and context_tokens >= 0:
                pct = max(0, min(100, round((context_tokens / context_length) * 100)))
                parts.append(f"{pct}%")
        elif field == "cwd":
            rel = _home_relative_cwd(cwd or os.environ.get("TERMINAL_CWD", ""))
            if rel:
                parts.append(rel)
        elif field == "route_reasoning":
            rr = _route_reasoning_label(
                provider=provider,
                model=model,
                reasoning_effort=reasoning_effort,
                route_label=route_label,
                codex_quota_used_percent=codex_quota_used_percent,
            )
            if rr:
                parts.append(rr)
                _route_reasoning_produced = True
        elif field == "response_ref":
            ref = str(response_ref or "").strip()
            if ref:
                parts.append(ref)
        # Unknown field names are silently ignored.

    # Fallback: when route_reasoning is in fields but had no data (e.g. no
    # reasoning_effort configured), show model as a standalone field so the
    # footer isn't reduced to just response_ref.
    if "route_reasoning" in fields and not _route_reasoning_produced:
        short = _model_short(model)
        if short:
            parts.insert(0, short)

    if not parts:
        return ""
    return _SEP.join(parts)


def _apply_footer_style(text: str, style: str | None) -> str:
    """Apply lightweight visual styling using standard markdown syntax."""
    if not text:
        return ""
    return f"*{text}*" if str(style or "").strip().lower() == "italic" else text


def build_footer_line(
    *,
    user_config: dict[str, Any] | None,
    platform_key: str | None,
    model: Optional[str],
    context_tokens: int,
    context_length: Optional[int],
    cwd: Optional[str] = None,
    provider: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    route_label: Optional[str] = None,
    codex_quota_used_percent: Any = None,
    response_ref: Optional[str] = None,
) -> str:
    """Top-level entry point used by gateway/run.py.

    Returns the footer text (empty string when disabled or no data).  Callers
    append this to the final response themselves, preserving a single blank
    line of separation.
    """
    cfg = resolve_footer_config(user_config, platform_key)
    if not cfg.get("enabled"):
        return ""
    line = format_runtime_footer(
        model=model,
        context_tokens=context_tokens,
        context_length=context_length,
        cwd=cwd,
        fields=cfg.get("fields") or _DEFAULT_FIELDS,
        provider=provider,
        reasoning_effort=reasoning_effort,
        route_label=route_label,
        codex_quota_used_percent=codex_quota_used_percent,
        response_ref=response_ref,
    )
    return _apply_footer_style(line, cfg.get("style"))
