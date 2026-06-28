# Adaptive Routing Plugin — Improvement Plan (Archived)

> **Date:** 2026-06-28
> **Status:** Completed — folded into the canonical user/developer docs.
> **Scope:** Original plan for significant improvements to the `adaptive-routing` plugin (manual/auto routing, smarter scoring, generic quota integration, manual model lock, route trace).

This document is retained as a historical record of the planned work. The
implementation is complete on `main` and the canonical, current description of
adaptive-routing behavior now lives in the user-facing docs. Please refer to
those instead of this file when answering "how does adaptive routing work
today?".

## Where the canonical docs live

- **User-facing overview:** [`website/docs/user-guide/features/built-in-plugins.md`](../website/docs/user-guide/features/built-in-plugins.md) → `### adaptive-routing`
  - Manual vs. auto routing, planned stacks, quota policy, and the diagnostic CLI surface.
- **Slash commands:** [`website/docs/reference/slash-commands.md`](../website/docs/reference/slash-commands.md) → `/model` and `/model auto` entries.
- **Runtime footer / quota display:** [`website/docs/user-guide/configuration.md`](../website/docs/user-guide/configuration.md) → "Runtime-metadata footer (gateway only)" and [`gateway/runtime_footer.py`](../../gateway/runtime_footer.py) → `_provider_shows_quota_in_footer()`.
- **Hook contract:** [`website/docs/user-guide/features/hooks.md`](../website/docs/user-guide/features/hooks.md) → `resolve_turn_route`.
- **Developer / architecture:** [`docs/manual/2026-06-20 Custom Features Pluginization Architecture Record.md`](../manual/2026-06-20%20Custom%20Features%20Pluginization%20Architecture%20Record.md) → `### 5.3 plugins/adaptive-routing/`.
- **Active customizations inventory:** [`docs/manual/2026-06-20 Hermes Active Customized Features.md`](../manual/2026-06-20%20Hermes%20Active%20Customized%20Features.md) → adaptive-routing entry.
- **Code:** [`plugins/adaptive-routing/`](../../plugins/adaptive-routing/) (`__init__.py`, `policy.py`, `manual_lock.py`, `trace.py`).
- **Tests:** [`tests/plugins/adaptive_routing/`](../../tests/plugins/adaptive_routing/), [`tests/gateway/test_model_auto_routing.py`](../../tests/gateway/test_model_auto_routing.py), [`tests/gateway/test_runtime_footer.py`](../../tests/gateway/test_runtime_footer.py).

## What shipped (summary)

- Plugin-owned balanced scoring engine in `plugins/adaptive-routing/policy.py` (`quality_fit + cost - quota_penalty - mismatch_penalty`, no latency). Tier classification (routine / difficult / complex) and reasoning-effort mapping are deterministic.
- Final-decision semantics on the `resolve_turn_route` hook contract: when the plugin returns a complete decision (`final_decision: true`), the gateway applies it directly and skips core `decide_turn_route()`. `None` or advisory results fall back to core routing.
- Manual model lock: `/model <provider:model>` sets a per-session lock on `GatewayRunner._session_model_lock`. While locked, `resolve_turn_route` returns a `final_decision` pinning the locked provider/model, bypassing adaptive scoring.
- `/model auto` clears the lock, drops the session model override, and evicts the cached agent so the next turn re-runs adaptive routing. There is **no separate `/routing` command** — the return-to-auto path lives entirely on `/model auto`. This replaces the originally-planned `/routing auto` command.
- Provider-agnostic `QuotaState` in `plugins/adaptive-routing/policy.py` (mirroring `CodexQuotaState` from `agent/reasoning_policy.py`). Quota snapshots are read through `gateway/quota_service` so hot gateway code never imports the account-usage plugin.
- Runtime footer quota display: only `openai-codex` / `codex` shows a 5-hour rolling used percentage. **Opencode Go and Opencode Zen do not display quota in the footer** (no supported quota source today). **DeepSeek PAYG omits quota** entirely. `display_in_footer` is the authoritative toggle, with `_provider_shows_quota_in_footer()` as the built-in fallback table.
- Diagnostics: `hermes route-diagnose "<message>"` runs the balanced scoring engine; `hermes route-trace [--clear]` dumps the bounded route-trace buffer (`plugins.adaptive_routing.trace.enabled: true` to record).
- Config namespace: `plugins.adaptive_routing.*` is the authoritative block; legacy `agent.reasoning_policy.*` keys overlay only missing fields for back-compat.

## Planned model stacks (as shipped)

| Stack | Routine | Difficult | Complex |
|---|---|---|---|
| Primary (Opencode Go + OpenAI OAuth) | `mimo-v2.5` (Opencode Go) | `minimax-m3` (Opencode Go) | `gpt-5.5` (OpenAI OAuth / Codex) |
| Codex fallback (OpenAI OAuth) | `gpt-5.4-mini` | `gpt-5.4` | `gpt-5.5` |
| DeepSeek fallback (PAYG) | `deepseek-v4-flash` | `deepseek-v4-pro` | `deepseek-v4-pro` |

The objective is **balanced** (quality vs. cost). Latency is not a criterion.

## Quota display policy (as shipped)

| Provider | Quota type | Window | Footer display |
|---|---|---|---|
| OpenAI OAuth (Codex) | Rolling | 5 hours | ✅ Shown |
| Opencode Go | Unknown / no source | — | ❌ Omitted |
| Opencode Zen | Unknown / no source | — | ❌ Omitted |
| DeepSeek PAYG | None (direct API) | — | ❌ Omitted |

The Opencode Go quota wiring originally planned under Phase 3 was deliberately
not landed: there is no public quota API to integrate against, and surfacing a
placeholder would be misleading. The footer omits quota for Opencode Go and
treats it as a neutral (small penalty, not a hard gate) provider in scoring.
A future change can add the branch when a real quota source exists.

## Deviation from the original plan

- **No `/routing` command.** The originally-planned `/routing auto` command was replaced by `/model auto`, which reuses the existing `/model` dispatcher and keeps a single command surface. See `tests/gateway/test_model_auto_routing.py`.
- **No Opencode Go quota footer.** The planned "Opencode Go five-hour quota" integration was deferred because no real quota source is available. The provider is still a first-class routing target (primary stack, routine/difficult tiers), but the footer shows the route label and reasoning effort only.
- **Manifest-specific private-fork routing/model customizations were removed.** The plan referenced a private-fork `Manifest` provider with custom routing; that path is no longer wired in the adaptive-routing policy. The shipped plugin uses only the canonical providers listed in the stacks table above.
- **Final hook contract came in earlier than planned.** The upstream `_resolve_turn_agent_config` already gates on `force_reasoning_config`, so the planned manual-model-lock gate is implemented as a hook return value rather than a separate gateway gate. Cache invariants are preserved.

## Validation (executed prior to archival)

- `tests/plugins/adaptive_routing/` and `tests/gateway/test_model_auto_routing.py`: all green. 298 tests passed, 0 failed, on the most recent pre-archival run.
- Targeted run `scripts/run_tests.sh tests/plugins/adaptive_routing/ tests/gateway/test_model_auto_routing.py tests/gateway/test_runtime_footer.py tests/agent/test_reasoning_policy.py -q` — green.
- `gateway/run.py` confirms `_session_model_lock` is the per-session model pin consumed by `resolve_turn_route`; `_session_model_overrides` is the legacy text-picker dict that `/model auto` clears alongside the lock.
- `gateway/runtime_footer.py::_provider_shows_quota_in_footer` matches the quota table above.
- `plugins/adaptive-routing/policy.py::DEFAULT_STACKS` and `DEFAULT_QUOTA_POLICY` match the planned stacks and quota policy exactly.

## If you need to make changes

When updating adaptive-routing behavior, edit the canonical docs first and let
this archived plan stand. The two main living surfaces are:

- `website/docs/user-guide/features/built-in-plugins.md` — user-facing description of the plugin (manual vs auto, stacks, diagnostics).
- `plugins/adaptive-routing/policy.py` and `__init__.py` — implementation, including the `DEFAULT_STACKS` and `DEFAULT_QUOTA_POLICY` tables.

If a behavior described in the canonical docs drifts from `policy.py`, fix the
docs to match the code (the code is the source of truth), then note the
correction in the changelog. This archived plan is not the place to record
live behavior.
