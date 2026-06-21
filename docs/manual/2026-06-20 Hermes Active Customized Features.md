# Hermes Active Customized Features — 20-06-2026

Generated: 20-06-2026 16:30 MYT
Repository: this private-fork checkout (relative to workspace root)
Upstream comparison: `upstream/main` or the configured NousResearch remote
Private fork push remote: configured private-fork remote for `lehmannm360/hermes-agent` (for example `agent` in earlier notes, or `origin` in this checkout)
Latest upstream integration: PR #4, merged after integration commit `1ae1434f7` brought in NousResearch upstream `5a53e0f0f`; final synced local/remote `main` is `3d3f55992`.

## Summary

This document lists the active private-fork customizations currently present in Esa's Hermes checkout, based on the private-fork delta, completed pluginization work on branch `custom/pluginize-active-features`, the completed upstream integration PR #4, and the active Hermes skill/reference notes.

Implementation and QA status as of the pluginization update:

- Steps 1-9 of the remaining active-feature pluginization work are implemented and documented as completed reference material.
- Final targeted implementation QA passed **399 tests, 0 failed, across 15 files**.
- The earlier focused account-usage run passed **48/48**, and account usage also passed in the broader QA run.
- The upstream integration PR #4 completed successfully: final `main` is `3d3f55992`, integration merge commit was `1ae1434f7`, upstream merged commit was `5a53e0f0f`, and post-integration QA passed **408 tests, 0 failed** across targeted upstream/plugin/custom-feature validation.
- Compiled Memory Architecture is intentionally excluded from this update and remains governed by its separate plan.

Future upstream-update guidance is maintained in `docs/manual/2026-06-20 Hermes Private Fork Upstream Update Playbook.md`. Use that playbook before the next upstream sync instead of relying on the stale changed-file snapshot that this document used to carry.

Current private-fork delta handling:

- Do not treat old changed-file lists as canonical after an upstream merge. They go stale as soon as upstream refactors or the private fork lands pluginization work.
- Recompute the active private-fork delta from git when needed. The recommended commands and review checklist live in `docs/manual/2026-06-20 Hermes Private Fork Upstream Update Playbook.md`.
- Use the feature descriptions below as the maintained source of truth for active custom behavior.
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

Pluginization state:

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

Pluginization state:

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

Pluginization state:

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

Pluginization state:

- Implemented partially. The plugin owns operator surfaces and hook callbacks; core still owns row/ref creation, persistence ordering, pruning/cascade behavior, and delivery safety.

---

### 5. Cross-platform message allowlist registry

Status: active hook-backed security customization
Primary files:

- `gateway/message_allowlist.py`
- `gateway/authz_mixin.py`
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
- After upstream PR #4, auth-related gateway seams live primarily in `gateway/authz_mixin.py`; future updates must re-check that file as well as `gateway/run.py` so allowlist/auth behavior is not silently dropped by upstream refactors.

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

Pluginization state:

- Implemented through `pre_gateway_authorize_message`. Plugin-disabled behavior falls back to core authorization.

---

### 6. Unauthorized DM behavior hardening

Status: active core security invariant around hook fire site
Primary files:

- `gateway/authz_mixin.py`
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

Pluginization state:

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

Pluginization state:

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

Pluginization state:

- No. Installer behavior is not plugin-shaped.

---

## Planned custom feature not yet implemented

### Compiled Memory Architecture

Status: planned, not implemented
Current plan location:

- Repository: `docs/plans/2026-06-01 Compiled Memory Architecture Implementation Plan.md`
- Drive reference, if maintained separately: `Docs/Plan/2026-06-01 Compiled Memory Architecture Implementation Plan.md`

Planned architecture:

- bundled plugin under `plugins/compiled-memory/`
- tests under `tests/plugins/compiled_memory/`
- integration via `post_llm_call`, `register_cli_command`, optional `register_tool`, and `plugins.entries`
- no edits to upstream-owned core files

Why it matters:

- It is the first major feature intentionally designed as plugin-first and merge-safe.

## Plugin migration priority

Completed pluginization sequence:

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
7. For feature branches, record `BASE=$(git rev-parse HEAD)` and verify the feature diff against `$BASE`, not against the upstream remote branch, because this private fork already has unrelated customizations.
8. Push private fork work to `agent`, not upstream.
9. During upstream merges, audit semantic conflicts in addition to textual conflicts. The PR #4 merge showed that upstream can move logic into new files such as `gateway/authz_mixin.py`, causing security seams to be lost without a direct conflict in `gateway/run.py`.
10. Re-run targeted tests after each upstream merge:
   - `tests/agent/test_reasoning_policy.py`
   - `tests/gateway/test_runtime_footer.py`
   - `tests/gateway/test_unauthorized_dm_behavior.py`
   - `tests/gateway/test_usage_command.py`
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

## Current changed files and recomputation guidance

Do not freeze a changed-file list in this document. After PR #4, upstream and private-fork history are aligned at `3d3f55992`, and old pre-upstream lists no longer describe the real delta. Recompute the list whenever a future update needs it.

Recommended commands:

```bash
PRIVATE_REMOTE=origin      # or agent, depending on this checkout
UPSTREAM_REMOTE=upstream   # or nous-upstream/nous, depending on this checkout
git fetch "$PRIVATE_REMOTE" main
git fetch "$UPSTREAM_REMOTE" main
git status --short
git diff --name-only "$UPSTREAM_REMOTE/main...$PRIVATE_REMOTE/main"
git diff --stat "$UPSTREAM_REMOTE/main...$PRIVATE_REMOTE/main"
```

Set `PRIVATE_REMOTE` to the configured `lehmannm360/hermes-agent` remote and `UPSTREAM_REMOTE` to the configured NousResearch remote before running the commands. For feature work after a local baseline, prefer a local base:

```bash
BASE=$(git rev-parse HEAD)
git diff --name-only "$BASE"...HEAD
```

When reviewing the recomputed list, map files back to the active-feature inventory above and to the playbook in `docs/manual/2026-06-20 Hermes Private Fork Upstream Update Playbook.md`. Pay special attention to semantic moves in gateway authorization, response persistence/session semantics, plugin discovery, config migrations, and GitHub workflow files.
