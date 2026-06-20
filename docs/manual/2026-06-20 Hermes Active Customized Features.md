# Hermes Active Customized Features — 20-06-2026

Generated: 20-06-2026 16:30 MYT  
Repository: `/Users/lehmann/.hermes/hermes-agent`  
Upstream comparison: `origin/main` = NousResearch upstream  
Private fork push remote: `agent` = `lehmannm360/hermes-agent`

## Summary

This document lists the active private-fork customizations currently present in Esa's Hermes checkout, based on the private-fork delta, completed pluginization work on branch `custom/pluginize-active-features`, and the active Hermes skill/reference notes.

Implementation and QA status as of the pluginization update:

- Steps 1-9 of the remaining active-feature pluginization work are implemented and ready for documentation/user review.
- Final targeted implementation QA passed **399 tests, 0 failed, across 15 files**.
- The earlier focused account-usage run passed **48/48**, and account usage also passed in the broader QA run.
- Compiled Memory Architecture is intentionally excluded from this update and remains governed by its separate plan.

Current private-fork delta versus `origin/main`:

- Changed/custom files listed below: representative, not exhaustive after the completed pluginization overlay.
- Net code/test delta: not recomputed after the final QA run; use the feature descriptions below as the current source of truth.
- Main touched areas: generic plugin hooks, quota service seam, gateway runtime/footer, message allowlist, adaptive routing, noiseless-failover policy, Codex account usage plugin, config, install scripts, tests.

## Active customized features

### 1. Adaptive reasoning and MiMo-first quota-aware model routing

Status: active hook-backed customization; bundled plugin present  
Primary files:

- `agent/reasoning_policy.py`
- `gateway/run.py`
- `gateway/quota_service.py`
- `hermes_cli/config.py`
- `hermes_cli/plugins.py`
- `plugins/adaptive-routing/`
- `tests/agent/test_reasoning_policy.py`
- `tests/plugins/adaptive_routing/`

What it does:

- Classifies task difficulty deterministically via keyword matching and task profile.
- Chooses reasoning effort by task profile.
- Routes MiMo as the primary adaptive provider: `mimo-v2.5` for easier tasks, `mimo-v2.5-pro` for hard/very_hard tasks.
- Falls back to Codex when MiMo is banned (timeout-based auto-fallback) or rate-limited.
- Uses Codex model selection by difficulty: `gpt-5.4-mini` for tiny/easy, `gpt-5.5` for medium/hard/very_hard.
- Supports low-quota / emergency-quota behavior for Codex (preserves Codex as long as possible between 2–4% quota, lets runtime fallback handle real errors).
- Falls back to DeepSeek last: `deepseek-v4-flash` for easier tasks, `deepseek-v4-pro` for harder tasks.
- Keeps the policy mostly pure and testable in `agent/reasoning_policy.py`.
- Registers a cache-safe `resolve_turn_route` hook through the bundled `plugins/adaptive-routing/` plugin.
- Uses the generic `gateway/quota_service.py` seam for quota/account snapshots instead of importing `plugins.account_usage` from hot gateway code.
- Ignores dangerous plugin-returned route keys that could mutate messages, history, tools, toolsets, system prompt, or memory.

Routing chain: MiMo (primary) → Codex (fallback) → DeepSeek (last resort)

Current config surface:

- `agent.reasoning_policy.enabled`
- `agent.reasoning_policy.mimo_provider` (default: `xiaomi`)
- `agent.reasoning_policy.mimo_flash_model` (default: `mimo-v2.5`)
- `agent.reasoning_policy.mimo_pro_model` (default: `mimo-v2.5-pro`)
- `agent.reasoning_policy.deepseek_provider`
- `agent.reasoning_policy.deepseek_flash_model`
- `agent.reasoning_policy.deepseek_pro_model`
- `agent.reasoning_policy.codex_primary_model` (default: `gpt-5.5`)
- `agent.reasoning_policy.codex_fast_model` (default: `gpt-5.4-mini`)
- `agent.reasoning_policy.codex_model_by_difficulty` (difficulty → model map)
- `agent.reasoning_policy.codex_low_quota_threshold_percent` (default: 4.0)
- `agent.reasoning_policy.codex_emergency_threshold_percent` (default: 2.0)
- `agent.reasoning_policy.low_quota_hard_task_behavior`
- Difficulty-to-reasoning mappings

Merge risk:

- Medium, because the policy is now behind a generic hook seam, but it still touches turn routing and `hermes_cli/config.py`.

Plugin migration candidate:

- Implemented through `resolve_turn_route`. Explicit `/reasoning` session overrides and `force_reasoning_config` remain core-owned and stronger than plugin decisions.

---

### 2. Quiet/no-noisy fallback behavior and stream warming detection

Status: active core behavior with policy-only bundled plugin  
Primary files:

- `agent/chat_completion_helpers.py`
- `gateway/run.py`
- `agent/agent_runtime_helpers.py`
- `hermes_cli/plugins.py`
- `plugins/gateway-noiseless-failover/`
- `tests/plugins/gateway_noiseless_failover/`

What it does:

- Suppresses user-facing fallback noise in gateway sessions.
- Logs fallback/rate-limit routing instead of emitting visible status messages.
- Adds a 90-second stream-warming warning before the full stale timeout.
- Preserves the user's preference that fallback should be quiet unless the final answer needs to mention it.
- Declares `transform_status_event` in the plugin hook registry for future status-event work.
- Keeps `plugins/gateway-noiseless-failover/` policy-only for now; no live status-event fire site exists yet.
- Preserves visibility for terminal failures, auth failures, billing failures, missing fallback provider, and content-policy blocks.

Merge risk:

- Medium, because fallback paths are central and likely to evolve upstream.

Plugin migration candidate:

- Partially implemented as a policy-only bundled plugin. Live suppression remains intentionally deferred until a safe `transform_status_event` fire site exists.

---

### 3. Codex account usage improvements

Status: active, pluginized; QA passed  
Primary files:

- `plugins/account_usage/usage.py`
- `plugins/account_usage/plugin.yaml`
- `tests/test_account_usage.py`
- `tests/plugins/account_usage/`
- `tests/gateway/test_usage_command.py`

What it does:

- Makes Codex usage lookup work even when credentials come from the credential pool rather than the legacy singleton token block.
- Treats Codex account ID as optional for usage endpoint routing.
- Supports usage/quota visibility needed by adaptive routing and runtime footers.
- Exposes a bundled plugin CLI surface for account usage inspection.
- Registers quota fetch/render callbacks through `gateway/quota_service.py` so gateway runtime/footer/routing callers do not import the plugin directly.
- Intentionally remains Codex-only. Anthropic account usage is confirmed unsupported for Hermes and is out of scope.

Validation:

- Earlier focused account-usage validation passed **48/48**.
- Account usage also passed in the final broader implementation QA run.
- The final targeted implementation QA run passed **399 tests, 0 failed, across 15 files**.

Merge risk:

- Low-medium.

Plugin migration candidate:

- Best first pilot. This is small, read-only, and now lives in a bundled plugin rather than core agent code.

---

### 4. Gateway runtime footer customization

Status: active hook-backed customization; response-ref persistence core-owned  
Primary files:

- `gateway/runtime_footer.py`
- `gateway/run.py`
- `gateway/session.py`
- `hermes_state.py`
- `hermes_cli/plugins.py`
- `plugins/gateway-runtime-metadata/`
- `tests/gateway/test_runtime_footer.py`
- `tests/test_hermes_state.py`
- `tests/plugins/gateway_runtime_metadata/`

What it does:

- Adds compact gateway runtime metadata footer behavior.
- Supports model/provider/reasoning/route/quota visibility in final replies where configured.
- Adds Hermes-generated response references such as `r-8f3a21c4`.
- Stores response-reference mappings in Hermes SQLite.
- Supports lookup/pruning behavior aligned with 180-day session retention and no tombstone/archive preference.
- Keeps footer visually subordinate and Telegram-compatible.
- Registers `format_gateway_runtime_footer` for optional footer override while default core footer behavior remains the fallback.
- Registers `on_final_response_persisted`, which fires only after the assistant DB row and response-reference mapping exist.
- Provides a bundled `response-ref` CLI lookup surface.
- Preserves the trailing-send footer path.

Merge risk:

- Medium-high, because it touches gateway send/session/storage paths.

Plugin migration candidate:

- Implemented partially. The plugin owns operator surfaces and hook callbacks; core still owns row/ref creation, persistence ordering, pruning/cascade behavior, and delivery safety.

---

### 5. Cross-platform message allowlist registry

Status: active hook-backed security customization  
Primary files:

- `gateway/message_allowlist.py`
- `gateway/run.py`
- `hermes_cli/plugins.py`
- `plugins/message-allowlist/`
- `tests/gateway/test_unauthorized_dm_behavior.py`
- `tests/hermes_cli/test_gateway.py`
- `tests/plugins/message_allowlist/`

What it does:

- Adds first-class config under `security.message_allowlist.members`.
- Lets profiles define team/member identities once instead of duplicating per-platform env vars.
- Supports member roles, permissions metadata, and platform account IDs.
- Currently seeds Esa as owner for Telegram user `637486142` in relevant profiles.
- Supports WhatsApp ID alias expansion for future WhatsApp work.
- Registers `pre_gateway_authorize_message`, which covers both cold message handling and active-session busy paths.
- Distinguishes allowlist configured/enabled state so enabling the plugin alone does not accidentally engage fail-closed enforcement.
- Fails closed when allowlist enforcement is enabled and hook callbacks do not explicitly allow.

Example shape:

```yaml
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
```

Merge risk:

- Medium-high, because it is security-sensitive and gateway-entry-path-sensitive.

Plugin migration candidate:

- Implemented through `pre_gateway_authorize_message`. Plugin-disabled behavior falls back to core authorization.

---

### 6. Unauthorized DM behavior hardening

Status: active core security invariant around hook fire site  
Primary files:

- `gateway/run.py`
- `tests/gateway/test_unauthorized_dm_behavior.py`
- `tests/plugins/message_allowlist/test_authorize_hook_contract.py`

What it does:

- Ensures unauthorized DM/chat handling follows the allowlist behavior.
- Prevents unapproved senders from reaching normal agent execution.
- Keeps authorization behavior test-covered.
- Preserves queue/control-command bypass behavior so `/stop`, `/new`, `/queue`, `/status`, `/approve`, and `/deny` can reach the runner when required.

Merge risk:

- Medium, due to gateway entry path coupling.

Plugin migration candidate:

- Implemented as a retained core invariant plus the message-allowlist plugin policy hook.

---

### 7. Per-session reasoning override precedence

Status: active core invariant  
Primary files:

- `gateway/run.py`
- `gateway/session.py`
- related gateway tests

What it does:

- Ensures explicit `/reasoning` session overrides take precedence over adaptive routing.
- Keeps the selected session model/provider stable when the user has explicitly steered reasoning.
- Prevents adaptive routing from silently overriding a deliberate per-session setting.

Merge risk:

- Medium, because it lives in the session and turn-routing path.

Plugin migration candidate:

- Not plugin-owned. `resolve_turn_route` exists, but the hook contract explicitly keeps forced/session reasoning overrides stronger than plugin decisions.

---

### 8. Install/update behavior cleanup

Status: active small installer customization  
Primary files:

- `scripts/install.sh`
- `setup-hermes.sh`

What it does:

- Keeps private fork install/update behavior aligned with Esa's setup.
- Current diff is tiny and should be rechecked during upstream merges.

Merge risk:

- Low-medium, because install scripts change upstream occasionally.

Plugin migration candidate:

- No. Installer behavior is not plugin-shaped.

---

## Planned custom feature not yet implemented

### Compiled Memory Architecture

Status: planned, not implemented  
Current plan location:

- Local: `~/.hermes/docs/plans/2026-06-01-compiled-memory-architecture-implementation-plan.md`
- Drive: `Docs/Plan/2026-06-01 Compiled Memory Architecture Implementation Plan.md`

Planned architecture:

- bundled plugin under `plugins/compiled-memory/`
- tests under `tests/plugins/compiled_memory/`
- integration via `post_llm_call`, `register_cli_command`, optional `register_tool`, and `plugins.entries`
- no edits to upstream-owned core files

Why it matters:

- It is the first major feature intentionally designed as plugin-first and merge-safe.

## Plugin migration priority

Recommended order:

1. `account-usage` plugin pilot — ✅ implemented/wired; focused QA passed 48/48.
2. Runtime footer helper/lookup plugin — ✅ implemented as `plugins/gateway-runtime-metadata/`; storage remains core-owned.
3. Gateway allowlist hook and plugin — ✅ implemented through `pre_gateway_authorize_message` with fail-closed enforcement when enabled.
4. Adaptive reasoning hook and plugin — ✅ implemented through cache-safe `resolve_turn_route`; session/forced overrides remain core-owned.
5. Quiet fallback policy plugin — ✅ policy-only implementation; live `transform_status_event` fire site intentionally deferred.
6. `compiled-memory` plugin — separate out-of-scope feature governed by its own plan.

## Merge-risk overview

Low risk:

- Codex account usage improvements
- tiny install/setup adjustments

Medium risk:

- quiet fallback behavior
- per-session reasoning override precedence
- runtime footer storage/formatting
- quota service seam

Higher risk:

- adaptive reasoning and MiMo-first quota-aware routing
- cross-platform message allowlist / unauthorized DM entry path

## Maintenance rules

1. Keep new features plugin-first when there is a real extension seam.
2. Do not force existing core patches into plugins until Hermes exposes the right hook.
3. Prefer tiny generic upstream-compatible hooks over large private patches.
4. Keep `transform_status_event` documented as declared-but-not-fired until a live fire site is implemented and tested.
5. Preserve response-ref persistence and row/ref ordering as core-owned even when footer/operator surfaces are pluginized.
6. Preserve route cache-safety: plugins must not mutate messages, history, toolsets, system prompts, or memory.
7. For feature branches, record `BASE=$(git rev-parse HEAD)` and verify the feature diff against `$BASE`, not against `origin/main`, because this private fork already has unrelated customizations.
8. Push private fork work to `agent`, not upstream.
9. Re-run targeted tests after each upstream merge:
   - `tests/agent/test_reasoning_policy.py`
   - `tests/gateway/test_runtime_footer.py`
   - `tests/gateway/test_unauthorized_dm_behavior.py`
   - `tests/hermes_cli/test_gateway.py`
   - `tests/plugins/gateway_runtime_metadata/`
   - `tests/plugins/message_allowlist/`
   - `tests/plugins/adaptive_routing/`
   - `tests/plugins/gateway_noiseless_failover/`
   - `tests/test_plugin_hooks.py`
   - `tests/test_quota_service.py`
   - `tests/test_account_usage.py`
   - `tests/test_hermes_state.py`

Account-usage validation note: include `tests/plugins/account_usage/test_codex_usage.py`, `tests/plugins/account_usage/test_plugin_load.py`, and `tests/gateway/test_usage_command.py` when validating the plugin pilot, and run them only through `scripts/run_tests.sh`.

## Current changed files vs `origin/main`

The list below is a representative active-feature inventory after pluginization, not an exhaustive diff. Generated docs and out-of-scope Compiled Memory files are intentionally excluded.

```text
agent/agent_runtime_helpers.py
agent/chat_completion_helpers.py
agent/reasoning_policy.py
cli.py
gateway/message_allowlist.py
gateway/quota_service.py
gateway/run.py
gateway/runtime_footer.py
gateway/session.py
hermes_cli/plugins.py
hermes_cli/config.py
hermes_state.py
plugins/account_usage/__init__.py
plugins/account_usage/plugin.yaml
plugins/account_usage/usage.py
plugins/adaptive-routing/__init__.py
plugins/adaptive-routing/plugin.yaml
plugins/gateway-noiseless-failover/__init__.py
plugins/gateway-noiseless-failover/plugin.yaml
plugins/gateway-runtime-metadata/__init__.py
plugins/gateway-runtime-metadata/plugin.yaml
plugins/message-allowlist/__init__.py
plugins/message-allowlist/plugin.yaml
scripts/install.sh
setup-hermes.sh
tests/agent/test_reasoning_policy.py
tests/gateway/test_runtime_footer.py
tests/gateway/test_unauthorized_dm_behavior.py
tests/hermes_cli/test_gateway.py
tests/plugins/adaptive_routing/
tests/plugins/account_usage/test_codex_usage.py
tests/plugins/account_usage/test_plugin_load.py
tests/plugins/gateway_noiseless_failover/
tests/plugins/gateway_runtime_metadata/
tests/plugins/message_allowlist/
tests/test_account_usage.py
tests/test_hermes_state.py
tests/test_plugin_hooks.py
tests/test_quota_service.py
```
