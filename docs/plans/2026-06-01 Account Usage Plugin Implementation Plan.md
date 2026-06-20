# Codex Account Usage Plugin As-Built Validation Plan

> **For Hermes:** This document was reviewed and revised for the 2026-06-20 migration workflow. The account usage plugin implementation is already complete and active in this checkout/fork. Treat this file as an as-built validation, rollback, and upstream-update checklist for the pilot rather than a greenfield implementation plan.

> **Validation update, 2026-06-20:** The implementation has passed QA in this checkout. The earlier focused account-usage run passed **48/48**, and account usage also passed in the broader pluginization QA run. The final targeted implementation QA run for steps 1-9 passed **399 tests, 0 failed, across 15 files**.

**Goal:** Validate and maintain the bundled Codex account usage plugin as the first pluginized private-fork customization, while keeping the pilot merge-safe and useful as a reference for future plugin-first migrations.

**Current architecture:**
The active feature lives in the bundled plugin package `plugins/account_usage/`. The operator command remains named `account-usage`, but the Python package and repository directory use the import-safe underscore form. The plugin owns Codex usage fetching, parsing, rendering, and plugin CLI registration. The completed follow-up pluginization work adds `gateway/quota_service.py` as the generic quota/account snapshot seam, so hot gateway footer/routing code no longer needs to import `plugins.account_usage` directly.

**Provider scope:**
The account usage plugin is Codex-only for Hermes. Anthropic account usage is confirmed unsupported for Hermes, is out of scope, and must not be included in implementation tasks, expected provider support, acceptance criteria, or fallback validation for this feature.

**Tech stack:**
Python, Hermes bundled plugin system, `httpx`, existing Codex auth helpers, and repository tests run through `scripts/run_tests.sh`.

---

## Plan Summary

This is the as-built record for the account usage plugin pilot.

The original plan was to move Codex account usage improvements out of core ownership and into a bundled plugin. That migration is now implemented. This revised plan no longer asks implementers to scaffold, move, or wire the feature from scratch. Instead, it documents what is live, how to validate it, how to keep it merge-safe during upstream updates, and how to roll back without touching credentials.

The pilot originally accepted minimal live core wiring. The 2026-06-20 pluginization follow-up keeps that wiring thin and moves runtime quota access behind the generic quota service seam. This differs from the Compiled Memory Architecture plan, which remains out of scope for this session.

## Non-goals

- Do **not** redesign Codex auth.
- Do **not** add Anthropic support back into this feature; Anthropic account usage is confirmed unsupported for Hermes and is intentionally out of scope.
- Do **not** change unrelated routing, footer, or gateway formatting behavior.
- Do **not** expand gateway/routing patches heavily to compensate for missing plugin discovery seams.
- Do **not** upload repository documentation to Drive as part of this plan.
- Do **not** treat the 399-test pluginization QA run as Compiled Memory Architecture validation; that feature remains out of scope.

## Existing behavior to preserve

- Codex usage resolves when credentials come from the credential pool.
- The Codex account ID remains optional.
- The usage snapshot renders the expected session and weekly quota lines.
- The `account-usage` operator CLI surface works through plugin registration.
- The gateway `/usage` command continues to respond.
- Runtime footer/routing helpers can consume quota snapshots when configured to do so.
- Gateway footer/routing consumers use `gateway/quota_service.py` for quota snapshots instead of importing the account-usage plugin directly.
- Gateway behavior remains safe even if quota footer visibility is disabled.
- Anthropic remains unsupported and absent from provider behavior for this Hermes account usage feature.

## Current implementation status

As of the 2026-06-20 documentation update, the account usage work is no longer a pending implementation plan:

- The bundled plugin package exists under `plugins/account_usage/`.
- The plugin registration path and service implementation are present in the plugin package.
- The operator-facing command remains `account-usage` while the package uses the import-safe `account_usage` name.
- CLI and gateway consumers are wired through thin compatibility imports rather than re-owning the implementation.
- Gateway `/usage` is documented as an intentional integration seam.
- Runtime footer/routing quota consumers use the generic `gateway/quota_service.py` seam; if the plugin is disabled, absent, or raises, quota lookup degrades to `None`/empty lines.
- Anthropic support has been removed from the feature scope and is documented as unsupported for Hermes.
- Targeted test importance remains high because this feature touches CLI, gateway, quota rendering, and routing/footer consumers. Current QA status: earlier focused account-usage validation passed 48/48 and broader pluginization QA passed.

## As-built inventory

### Plugin package

The bundled plugin currently consists of:

- `plugins/account_usage/__init__.py`
- `plugins/account_usage/plugin.yaml`
- `plugins/account_usage/usage.py`

There is no separate plugin `cli.py`. CLI registration lives in `plugins/account_usage/usage.py` via `register_plugin(ctx)`.

### Plugin tests

Current plugin test coverage lives under `tests/plugins/account_usage/` and includes:

- `tests/plugins/account_usage/test_codex_usage.py`
- `tests/plugins/account_usage/test_plugin_load.py`

Rendering assertions are folded into `tests/plugins/account_usage/test_codex_usage.py`; there is no separate `tests/plugins/account_usage/test_rendering.py`.

### Legacy and related tests

Related coverage also includes:

- `tests/test_account_usage.py`
- `tests/gateway/test_usage_command.py`

### Deliberate minimal core touchpoints

The pilot intentionally keeps small core wiring and now uses a generic quota service for runtime consumers:

- `cli.py` lazy-imports the plugin service so the CLI can invoke account usage without re-owning the implementation.
- `gateway/run.py` preserves `/usage` behavior and the `gateway.run.fetch_account_usage` monkeypatch surface used by `tests/gateway/test_usage_command.py`, while avoiding direct hot-path account-usage imports for quota snapshots.
- `gateway/quota_service.py` exposes `fetch_quota_snapshot()` and `render_quota_lines()` as the runtime seam for footer/routing helpers.
- `gateway/runtime_footer.py` and adaptive routing consumers may consume quota snapshots through `gateway/quota_service.py`.

These touchpoints are thin integration seams, not stale core ownership. If future plugin CLI registration or service discovery proves insufficient for runtime consumers, document the missing plugin accessor or hook and add a generic seam instead of expanding gateway/routing-specific patches.

### Generic quota service seam

`gateway/quota_service.py` is the completed generic account/quota snapshot seam. The account-usage plugin registers its fetcher and renderer during plugin discovery. Public accessors return `None` or an empty line list when the plugin is disabled, not loaded, or raises. This keeps plugin-disabled behavior safe for gateway footers and adaptive routing.

### Upstream mirror

`agent/account_usage.py` is a dormant upstream mirror and is clean versus `origin/main` in this checkout. It is not the live shim for the plugin. Do not treat it as active ownership unless a rollback explicitly re-points consumers to it.

## As-built validation sequence

Use this sequence when validating the active implementation, reviewing a private-fork update, or preparing an upstream merge.

### 1. Confirm plugin metadata and discovery

Validate that `plugins/account_usage/plugin.yaml` loads and that `plugins/account_usage/__init__.py` exposes the expected plugin registration path.

Targeted test command:

```bash
scripts/run_tests.sh tests/plugins/account_usage/test_plugin_load.py -v
```

Expected result: plugin metadata and plugin registration tests pass.

### 2. Confirm Codex usage behavior

Validate credential-pool behavior, optional account-id behavior, URL/header handling, parsing, and rendering in the plugin service.

Targeted test command:

```bash
scripts/run_tests.sh tests/plugins/account_usage/test_codex_usage.py -v
```

Expected result: Codex usage tests pass, including rendering assertions.

### 3. Confirm legacy compatibility coverage

Validate any retained compatibility expectations and ensure dormant upstream mirror behavior has not drifted unintentionally from the plugin contract.

Targeted test command:

```bash
scripts/run_tests.sh tests/test_account_usage.py -v
```

Expected result: existing account-usage tests pass or are intentionally updated to reflect the plugin as the live source of truth.

### 4. Confirm gateway `/usage` behavior

Validate that the gateway command path still imports the plugin service correctly and that `gateway.run.fetch_account_usage` remains patchable for `tests/gateway/test_usage_command.py`.

Targeted test command:

```bash
scripts/run_tests.sh tests/gateway/test_usage_command.py -v
```

Expected result: gateway `/usage` tests pass and the gateway still responds when quota/footer visibility is unavailable or disabled.

### 5. Confirm footer/routing consumption where changed

If a change touches footer or routing quota consumption, run the relevant focused gateway/footer tests. Use repository search to identify the exact test file if names change upstream.

Example targeted command when runtime footer behavior is touched:

```bash
scripts/run_tests.sh tests/gateway/test_runtime_footer.py -v
```

Expected result: footer/routing consumers tolerate available, unavailable, and disabled quota snapshots without breaking normal gateway responses.

### 7. Confirm quota service degradation

When account-usage, footer, or routing code changes, validate that `gateway/quota_service.py` returns safe empty results when no fetcher/renderer is registered and swallows provider errors.

Targeted test command:

```bash
scripts/run_tests.sh tests/test_quota_service.py -v
```

Expected result: disabled or failing quota providers never block gateway responses or routing decisions.

### 6. Confirm manual inventory gate

If the feature inventory changes, update the repository manual at `docs/manual/2026-06-20 Hermes Active Customized Features.md` in a separate documentation change. This is a repo-relative manual update gate only. Do not upload Drive copies and do not use `drive get` as part of this plan.

## Pre-upstream-update safety checks

Use separate diff bases for separate purposes.

### Feature diff scope in a private fork

For this feature's own diff in a private fork, record a local base before applying or reviewing the account-usage changes:

```bash
BASE=$(git rev-parse HEAD)
```

After changes, inspect the private-fork feature scope with:

```bash
git diff --name-only "$BASE"...HEAD
```

Do not claim account usage diffs are limited to plugin/test paths only. This pilot intentionally uses minimal live core wiring. Expected files may include the plugin package, plugin/legacy/gateway tests, and the deliberate core touchpoints described above.

### Retired-core-path restoration checks

Use `origin/main` path checks only when verifying whether a retired core path was restored cleanly to upstream state:

```bash
git diff --quiet origin/main -- agent/account_usage.py
```

Apply that pattern only to paths that are supposed to be clean versus upstream, such as the dormant `agent/account_usage.py` mirror in this checkout. Do not use it to reason about the whole account-usage feature diff.

### Expected minimal core wiring

When reviewing upstream merges, treat these as intentional thin wiring if they remain small and documented:

1. `cli.py` lazy-imports the plugin service.
2. `gateway/run.py` keeps `/usage` behavior and the `gateway.run.fetch_account_usage` monkeypatch target.
3. `gateway/quota_service.py` mediates quota snapshot fetch/render behavior for footer/routing helpers.
4. `gateway/runtime_footer.py` and route consumers may call the quota service seam, not `plugins.account_usage` directly.

If upstream refactors gateway dispatch, the `gateway.run.fetch_account_usage` patch target used by `tests/gateway/test_usage_command.py` may need to move. Update the test seam deliberately rather than deleting coverage silently.

### Workspace caveat

This checkout may show zero account-usage delta versus `origin/main` if the relevant migration has already landed or the branch is aligned. Validate rollback and diff expectations against the real private fork when preparing a private-fork upstream update.

## Rollback and fallback guidance

Rollback should be scoped to the failing seam and should not alter credentials, tokens, account IDs, or credential-pool configuration.

1. Prefer disabling the plugin surface if the plugin system supports doing so cleanly for the affected environment.
2. If CLI usage fails but gateway behavior is safe, re-point only the live CLI import in `cli.py` back to `agent.account_usage` while preserving credentials.
3. If gateway `/usage` fails, re-point only the command seam in `gateway/run.py` and verify the command still responds.
4. If footer/routing quota consumption fails, disable quota footer visibility or let `gateway/quota_service.py` degrade to unavailable snapshots; do not reintroduce direct plugin imports into hot gateway paths.
5. Keep `agent/account_usage.py` as an upstream mirror unless the rollback explicitly needs it as the live source.
6. After rollback, run the targeted tests for the changed seam with `scripts/run_tests.sh`.

Minimum rollback verification:

```bash
scripts/run_tests.sh tests/gateway/test_usage_command.py tests/test_account_usage.py -v
```

Expected result: the gateway still responds, account usage fallback behavior is understandable, and credential state remains untouched.

## Acceptance and maintenance criteria

The pilot remains healthy when all applicable criteria are true:

- [x] Plugin metadata exists at `plugins/account_usage/plugin.yaml`.
- [x] The plugin package imports through `plugins/account_usage/__init__.py`.
- [x] The `account-usage` CLI surface is registered through `plugins/account_usage/usage.py` for operator use.
- [x] Credential-pool-backed Codex usage is implemented in the plugin service.
- [x] Missing optional Codex account ID is handled by the plugin service.
- [x] Rendering support for session and weekly quota output expectations is implemented in plugin tests.
- [x] Gateway `/usage` is wired through the plugin service and preserves the current monkeypatch seam used by gateway tests.
- [x] Runtime footer/routing consumers are documented as intentional consumers of quota snapshots through `gateway/quota_service.py`.
- [x] Deliberate minimal core wiring is documented and remains thin.
- [x] Generic quota service seam exists and degrades safely when the account-usage plugin is disabled or unavailable.
- [x] Anthropic account usage is documented as unsupported and out of scope for Hermes.
- [x] No stale core ownership remains; `agent/account_usage.py` is either clean versus upstream or explicitly designated as the live rollback source.
- [ ] Drift between `plugins/account_usage/usage.py` and dormant upstream `agent/account_usage.py` is checked, or a single-source-of-truth decision is documented before accepting divergence.
- [x] Repository manual inventory is updated at `docs/manual/2026-06-20 Hermes Active Customized Features.md` when the inventory changes; no Drive upload is required.
- [ ] Upstream merge checks pass for the affected account-usage paths and intentional core touchpoints.
- [x] Focused account-usage QA passed 48/48 and broader pluginization QA passed.
- [x] Final targeted implementation QA for steps 1-9 passed 399 tests, 0 failed, across 15 files.

## Suggested maintenance workflow

1. Review the feature diff against the private-fork `BASE` for actual account-usage changes.
2. Check retired upstream mirror paths with `git diff --quiet origin/main -- <path>` only when they are expected to be clean.
3. Verify plugin metadata and service behavior with targeted plugin tests.
4. Verify CLI, gateway, quota service, and footer/routing consumers only for touched seams.
5. Check plugin-versus-upstream-mirror drift and document whether the plugin or the mirror is the source of truth.
6. Update the repo-relative active-features manual if inventory changed.
7. Keep rollback notes current with the actual live imports in `cli.py`, `gateway/run.py`, and `gateway/runtime_footer.py`.

## Notes

- Keep secrets out of the plan and out of validation output.
- Keep credential changes untouched during validation and rollback.
- Keep the plugin bundled and merge-safe.
- Prefer small, reversible changes.
- Treat this as a pilot for plugin-first customization, while recognizing that runtime quota consumption now uses the generic quota service seam and Compiled Memory Architecture remains outside this validation scope.
