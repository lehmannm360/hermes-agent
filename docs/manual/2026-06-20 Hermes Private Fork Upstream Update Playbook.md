# Hermes Private Fork Upstream Update Playbook — 20-06-2026

**Status:** Active best-practice playbook after upstream integration PR #4.
**Last validated merge:** PR #4, final `main` at `3d3f55992`.
**Integration commit:** `1ae1434f7`, merging NousResearch upstream `5a53e0f0f`.
**Validation result:** 408 tests passed, 0 failed across targeted upstream/plugin/custom-feature validation.
**Related records:** `docs/manual/2026-06-20 Hermes Active Customized Features.md`, `docs/manual/2026-06-20 Custom Features Pluginization Architecture Record.md`, `docs/manual/2026-06-01 Codex Account Usage Plugin As-Built Validation Record.md`, `docs/plans/2026-06-01 Compiled Memory Architecture Implementation Plan.md`.

## Purpose

Use this playbook before the next upstream merge into the private fork. It captures the successful PR #4 workflow and the pitfalls discovered during that merge so future updates do not rely on memory or stale changed-file lists.

The goal is not to avoid all conflicts. The goal is to make conflicts predictable, preserve plugin seams, detect semantic losses that textual merge tools miss, and validate the result through repeatable test commands.

## Merge facts from PR #4

- GitHub PR: `https://github.com/lehmannm360/hermes-agent/pull/4`.
- Final synced local/remote `main`: `3d3f55992`.
- Integration merge commit before PR merge: `1ae1434f7`.
- NousResearch upstream commit merged: `5a53e0f0f`.
- QA: 408 passed, 0 failed.
- Main conflict/resolution files encountered: `gateway/authz_mixin.py`, `gateway/run.py`, `gateway/session.py`, `hermes_cli/plugins.py`, `hermes_state.py`, `website/docs/user-guide/features/hooks.md`.
- GitHub push pitfall: remote push was blocked until the GitHub token gained `workflow` scope because upstream changed `.github/workflows/build-windows-installer.yml`.

## Pre-update checklist

1. Start from a clean tree:

   ```bash
   git status --short
   ```

2. Confirm remotes and identify which remote is private fork versus upstream:

   ```bash
   git remote -v
   ```

3. Set local remote names and fetch both remotes:

   ```bash
   PRIVATE_REMOTE=origin      # or agent, depending on this checkout
   UPSTREAM_REMOTE=upstream   # or nous-upstream/nous, depending on this checkout
   git fetch "$PRIVATE_REMOTE" main
   git fetch "$UPSTREAM_REMOTE" main
   ```

   Set `PRIVATE_REMOTE` to the configured `lehmannm360/hermes-agent` remote and `UPSTREAM_REMOTE` to the configured NousResearch remote before running later commands.

4. Record the current private-fork baseline:

   ```bash
   BASE=$(git rev-parse HEAD)
   git rev-parse "$PRIVATE_REMOTE/main"
   git rev-parse "$UPSTREAM_REMOTE/main"
   ```

5. Recompute current private-fork delta instead of trusting any frozen list:

   ```bash
   git diff --name-only "$UPSTREAM_REMOTE/main...$PRIVATE_REMOTE/main"
   git diff --stat "$UPSTREAM_REMOTE/main...$PRIVATE_REMOTE/main"
   ```

6. Read the active customization inventory before conflict work:

   - `docs/manual/2026-06-20 Hermes Active Customized Features.md`
   - `docs/manual/2026-06-20 Custom Features Pluginization Architecture Record.md`
   - `docs/manual/2026-06-01 Codex Account Usage Plugin As-Built Validation Record.md`
   - `docs/plans/2026-06-01 Compiled Memory Architecture Implementation Plan.md`

## Branch and PR workflow

Use a dedicated integration branch and keep `main` untouched until the merge is validated.

```bash
git switch main
git pull --ff-only "$PRIVATE_REMOTE" main
git switch -c update/upstream-YYYY-MM-DD
git merge --no-ff "$UPSTREAM_REMOTE/main"
```

After local validation, push the integration branch to the private fork remote and open a PR into private-fork `main`. Do not push to NousResearch upstream.

```bash
git push "$PRIVATE_REMOTE" update/upstream-YYYY-MM-DD
```

If GitHub rejects the push because workflow files changed, update the token/credential to include `workflow` scope. Do not drop upstream workflow changes solely to bypass the permission error.

## Dry-run merge and conflict detection

Before investing in manual conflict resolution, use a disposable branch or worktree to see the conflict surface.

```bash
git switch -c dryrun/upstream-YYYY-MM-DD
git merge --no-commit --no-ff "$UPSTREAM_REMOTE/main"
git status --short
git merge --abort
```

Treat the dry run as a map, not the final merge. It shows textual conflicts, but it will not catch semantic seam losses caused by upstream moving code to new files.

## Conflict resolution strategy for plugin seams

Resolve conflicts by preserving the generic seam and plugin-disabled fallback, not by mechanically keeping the private-fork side.

For each conflicted path:

1. Identify upstream intent and whether upstream moved or renamed the call site.
2. Re-apply only the smallest private-fork seam needed for active custom behavior.
3. Keep plugin-owned implementation under `plugins/<feature>/` and tests under `tests/plugins/<feature>/`.
4. Keep core changes generic: hook fire sites, no-op/degrade behavior, quota service seam, and retained security/session invariants.
5. Avoid direct hot-path imports from plugins when a service seam exists.

## Semantic conflict audit beyond textual conflicts

After all textual conflicts are resolved, run a semantic audit. PR #4 showed this is mandatory.

Checklist:

- Search for moved authorization, session, footer, plugin-discovery, and quota-service call sites.
- Verify old seam locations were not bypassed by new upstream helpers or mixins.
- Confirm tests still exercise the live path, not a dead compatibility path.
- Inspect the post-merge diff for unrelated deletions or reverted upstream fixes:

  ```bash
  git diff HEAD~1..HEAD
  ```

- Run `git diff --check` before tests:

  ```bash
  git diff --check
  ```

## Known pitfalls from PR #4

### Authorization mixin seam loss

Upstream extracted authorization into `gateway/authz_mixin.py`. The message-allowlist and authorization seams had to be re-injected there to avoid silent loss of the fail-closed security path.

Future audit:

```bash
git grep -n "pre_gateway_authorize_message\|authorize\|allowlist" -- gateway
```

Required preservation checks:

- cold messages still pass through the allowlist authorization hook when enforcement is enabled;
- active-session busy messages are covered;
- hook errors deny when allowlist enforcement is enabled;
- control and approval commands that must reach the runner still bypass both message guards.

### Response-reference and session return semantics

Conflicts in `gateway/session.py` and `hermes_state.py` can appear safe textually while changing response-ref persistence ordering.

Required preservation checks:

- assistant DB row exists before `on_final_response_persisted` fires;
- response-reference mapping points to the persisted assistant row;
- cascade/pruning behavior remains aligned with session retention;
- final response delivery is never blocked by lookup/notification hook failures.

### Plugin hook preservation

Conflicts in `hermes_cli/plugins.py` can drop hook names, change aggregation behavior, or alter failure isolation.

Required preservation checks:

- `pre_gateway_authorize_message` remains a valid hook;
- `format_gateway_runtime_footer` remains a valid hook;
- `on_final_response_persisted` remains a valid hook;
- `resolve_turn_route` remains a valid hook;
- `transform_status_event` remains declared but not treated as live unless a future change adds and tests a fire site.

### Config migration and version checks

Upstream often changes `hermes_cli/config.py`. Before accepting a merge:

- distinguish adding keys from renaming or restructuring keys;
- do not bump `_config_version` for a simple new key that deep-merge handles;
- do bump and migrate when renaming or transforming existing user config;
- keep non-secret behavior settings in `config.yaml`, not `.env`;
- verify plugin config namespaces do not shadow current source-of-truth keys accidentally.

### GitHub workflow-scope push failure

If upstream changes `.github/workflows/**`, a GitHub token without `workflow` scope may reject the push even when the merge is correct.

Resolution:

1. Update the GitHub credential/token to include `workflow` scope.
2. Retry the push.
3. Do not rewrite history or drop workflow changes merely to bypass the auth failure.

## Required validation commands

Always use `scripts/run_tests.sh`, not direct `pytest`.

Minimum custom-feature validation after an upstream merge:

```bash
scripts/run_tests.sh \
  tests/agent/test_reasoning_policy.py \
  tests/gateway/test_runtime_footer.py \
  tests/gateway/test_unauthorized_dm_behavior.py \
  tests/gateway/test_usage_command.py \
  tests/hermes_cli/test_gateway.py \
  tests/plugins/account_usage/test_codex_usage.py \
  tests/plugins/account_usage/test_plugin_load.py \
  tests/plugins/adaptive_routing/ \
  tests/plugins/gateway_noiseless_failover/ \
  tests/plugins/gateway_runtime_metadata/ \
  tests/plugins/message_allowlist/ \
  tests/test_account_usage.py \
  tests/test_hermes_state.py \
  tests/test_plugin_hooks.py \
  tests/test_quota_service.py \
  -v
```

Add upstream-adjacent tests based on files touched by the merge. For example, if upstream changes gateway sessions, include the relevant gateway/session tests discovered by search.

Before pushing or opening the PR:

```bash
git status --short
git diff --check
```

## Rollback and safety guidance

Prefer scoped rollback over broad reverts.

1. If a plugin-owned behavior fails, disable the affected plugin or plugin entry first.
2. If routing fails, disable `adaptive-routing` and rely on configured provider/model defaults.
3. If quota snapshots fail, let `gateway/quota_service.py` degrade to unavailable snapshots; do not reintroduce direct plugin imports into hot gateway paths.
4. If footer/response-ref behavior fails, disable footer visibility or runtime-metadata plugin callbacks before touching storage.
5. If authorization behavior fails, preserve fail-closed semantics while diagnosing; do not weaken security to make tests pass.
6. If a GitHub push fails due to workflow scope, fix credentials instead of modifying source.
7. Keep credentials, tokens, account IDs, and credential-pool files untouched during rollback.

Minimum rollback verification:

```bash
scripts/run_tests.sh \
  tests/gateway/test_runtime_footer.py \
  tests/gateway/test_unauthorized_dm_behavior.py \
  tests/gateway/test_usage_command.py \
  tests/agent/test_reasoning_policy.py \
  tests/test_hermes_state.py \
  tests/test_quota_service.py \
  -v
```

## Update completion record template

Append a short record to the relevant docs after each upstream integration:

```text
Upstream integration update, YYYY-MM-DD:
- PR: <private-fork PR URL or number>
- Final main: <commit>
- Integration merge commit: <commit>
- Upstream commit merged: <commit>
- QA: <passed>/<total> passed
- Notable semantic conflicts: <files and lesson>
- Push/auth pitfalls: <none or details>
```

Keep this playbook operational and concise. Move feature-specific architecture details back to the relevant manual record or active plan rather than expanding this file into a general design document.

## Integration records

### Upstream integration update, 2026-06-28

- Integration branch: `update/upstream-2026-06-28` (off private-fork `main` `b6dbbd304`).
- Integration merge commit: `0abe7b9a4` (private-fork side; merge --no-ff).
- Upstream commit merged: `135f23516` (NousResearch `hermes-agent` `main`).
- Private-fork baseline before merge: `b6dbbd304` (pre-merge plan housekeeping commit on `709067b84`).
- Upstream delta: 1074 commits since previous integration (`3d3f55992`); 1572 files changed; 2 textual conflicts, both resolved.
- Textual conflicts:
  - `hermes_cli/plugins.py` — content conflict in `STANDARD_HOOKS`. Private-fork added 5 gateway pluginization hooks (`pre_gateway_authorize_message`, `format_gateway_runtime_footer`, `on_final_response_persisted`, `resolve_turn_route`, `transform_status_event`); upstream added 3 kanban hooks (`kanban_task_claimed`, `kanban_task_completed`, `kanban_task_blocked`). Sets non-overlapping; both kept in the merged `STANDARD_HOOKS`.
  - `docs/plans/gemini-oauth-provider.md` — rename/delete. Private fork moved the planning doc from `plans/` to `docs/plans/`; upstream deleted the pre-rename copy. Kept the private-fork's `docs/plans/gemini-oauth-provider.md` (no upstream replacement exists).
- Semantic audit: all 5 private-fork hook fire sites confirmed intact in post-merge `gateway/run.py` (lines 3736, 4944, 8255, 10823, 11163). `gateway/authz_mixin.py`, `gateway/quota_service.py`, `gateway/runtime_footer.py`, `gateway/session.py`, `hermes_state.py` all merged cleanly via upstream — no private-fork seam re-application needed.
- Test-fix commit on top of merge: `4feb389c5` (`tests/gateway/test_compression_failure_session_sync.py` — updated mock lambda to accept new `reasoning_config` / `force_reasoning_config` kwargs that the upstream `_resolve_turn_agent_config` call site now passes).
- QA result (subset-level runs, see merge commit `0abe7b9a4` for full commands):
  - `tests/test_plugin_hooks.py` + `tests/test_quota_service.py` + `tests/test_hermes_state.py` + `tests/gateway/test_runtime_footer.py` + `tests/gateway/test_unauthorized_dm_behavior.py` + `tests/gateway/test_usage_command.py` + `tests/agent/test_reasoning_policy.py`: **412 passed / 0 failed**.
  - `tests/plugins/account_usage/` + `tests/plugins/adaptive_routing/` + `tests/plugins/gateway_noiseless_failover/` + `tests/plugins/gateway_runtime_metadata/` + `tests/plugins/message_allowlist/` + `tests/test_account_usage.py` + `tests/hermes_cli/test_gateway.py`: **90 passed / 0 failed**.
  - `tests/hermes_state/` + `tests/agent/test_reasoning_policy.py`: **55 passed / 0 failed**.
  - `tests/gateway/` full directory sweep: **232 passed / 35 failed** in 20-worker parallel mode. All 35 failures traced to one of two causes:
    1. **Cross-test pollution in 20-worker parallel mode** — 4 files (`test_bluebubbles.py`, `test_discord_attachment_download.py`, `test_msgraph_webhook.py`, `test_compression_failure_session_sync.py`) pass cleanly when run in isolation. The `test_compression_failure_session_sync.py` failure was a real kwarg-mismatch (fixed in `4feb389c5`); the other 3 are env-bleed artifacts that resolve on isolated runs.
    2. **Pre-existing upstream issues** — 5 files (`test_matrix.py::test_matrix_markdown_preserves_table_structure`, `test_shutdown_forensics.py`, `test_wecom_callback.py`, `test_background_command.py`, `test_gateway_shutdown.py`) fail in isolation on both this branch AND on a clean clone of `nous-upstream/main` at the same commit. These are not regressions from this merge.
- Notable conflicts and lessons:
  - The 1074-commit delta merged in **one conflict pair** because private-fork customization is heavily pluginized under `plugins/adaptive-routing/`, `plugins/account_usage/`, `plugins/message-allowlist/`, `plugins/gateway-noiseless-failover/`, and `plugins/gateway-runtime-metadata/`. The core/gateway files only carry the hook fire sites (small additions) which upstream did not touch, so the merges in `gateway/run.py`, `gateway/authz_mixin.py`, `gateway/quota_service.py`, `gateway/runtime_footer.py`, `gateway/session.py`, and `hermes_state.py` were trivial.
  - The one true content conflict in `hermes_cli/plugins.py` was a `STANDARD_HOOKS` set-union — exactly the case the pluginization architecture was designed for. Both sets are required for plugin manifests to resolve; merging them is mechanical and safe.
  - `git diff --check` after the merge shows whitespace warnings (trailing whitespace, new blank line at EOF) only in upstream-touched files — no private-fork files were flagged. Working tree is clean.
  - Plan housekeeping (the untracked `docs/plans/adaptive-routing-improvement-plan.md` file that started this work) was preserved as a dedicated commit `b6dbbd304` before the merge so the plan's history is reviewable.
- Plan updates: `docs/plans/adaptive-routing-improvement-plan.md` was updated post-merge to refresh the line-number table (e.g. `gateway/run.py:3472` → `gateway/run.py:3697`, `gateway/run.py:3408` → `gateway/run.py:3625`) and to add a new section §10 documenting the post-merge state, what changed upstream that the plan must respect, and the validation result.
- Push/auth pitfalls: none. Integration branch is local-only. (Per playbook, private-fork token may need `workflow` scope when the integration branch is pushed because upstream changed `.github/workflows/*` — verify before `git push origin update/upstream-2026-06-28`.)
