# Compiled Memory Architecture — Implementation Plan

**Date:** 01-06-2026 MYT  
**Planning model:** Codex 5.5, xhigh-reasoning planning pass  
**Revised:** 01-06-2026 — re-architected for merge-safety against `origin/main` (NousResearch upstream) and updated with merge-hardening review notes  
**Repository:** `/Users/lehmann/.hermes/hermes-agent` (`origin = NousResearch/hermes-agent` upstream, `agent = lehmannm360/hermes-agent` private fork push remote)  
**Owner:** Hermes Agent / default profile  
**Status:** Ready for implementation — **packaged as a bundled plugin** (`plugins/compiled-memory/`)

> **Revision note.** The original plan modified three of the largest, most
> frequently-churned files in the tree (`agent/conversation_loop.py` ≈262 KB,
> `hermes_cli/main.py` ≈568 KB, `hermes_cli/config.py` ≈264 KB). Every upstream
> merge would risk a conflict in exactly those files. This revision moves the
> **entire** customization into a self-contained bundled plugin and integrates
> through Hermes' existing extension points — **zero edits to any upstream
> file.** See §2.5. All functional design below (schemas, phases, heuristics,
> guardrails, tests) is unchanged; only *where the code lives* and *how it wires
> in* changed.

## 1. Goal

Implement **Compiled Memory Architecture** as an augmentation of Hermes’ existing memory system, not a replacement.

The system should make existing memory surfaces behave like compiled artifacts:

- Raw signals are captured safely.
- Feedback and repeated failures are stored as raw input, not promoted directly into durable memory.
- A schema-driven compiler/linter routes, reconciles, verifies, logs, and suppresses memory proposals.
- Existing compiled surfaces remain the durable authority:
  - `memories/MEMORY.md`
  - `memories/USER.md`
  - `skills/` and skill `references/`
  - Obsidian/docs/plans where configured
  - optional bounded domain wiki folders
  - eval/regression datasets
- Sleep consolidation evolves from a prompt-only summarization job into a compiler/linter workflow.

The target consolidation contract is:

1. **Orient** — load registry, recent feedback, recurrence patterns, enabled surfaces.
2. **Extract** — normalize raw signals into candidate preferences/facts/workflows/tool lessons/routing lessons.
3. **Route** — map candidates to the right durable surface.
4. **Reconcile** — dedupe, merge, identify conflicts/superseded entries.
5. **Verify** — require evidence before promotion.
6. **Log** — record what was proposed, applied, skipped, or escalated.
7. **Suppress** — keep low-confidence/noisy/stale signals out of durable memory.

## 2. Non-goals for v1

- Do **not** replace Hermes memory with standalone LLM Wiki.
- Do **not** auto-mutate `MEMORY.md`, `USER.md`, skills, docs, or plans directly from captured feedback.
- Do **not** change frozen memory snapshot behavior or prompt-cache invariants.
- Do **not** refresh memory injection mid-session.
- Do **not** write cross-profile files.
- Do **not** create autonomous mutation cron jobs in v1.
- Do **not** expose broad model-facing mutation tools until internal APIs and tests are stable.

## 2.5 Architecture decision — merge-safe plugin packaging

**Principle: touch zero upstream files.** Merge conflicts come from editing files
that upstream also edits. Hermes already ships a rich plugin system
(`hermes_cli/plugins.py`) whose extension points line up 1:1 with everything this
plan needs. We ship the whole feature as one bundled plugin and wire in through
those seams instead of editing core files.

### The three core-file edits are all unnecessary

| Original plan edit | Replace with (existing seam) | Verified at |
| --- | --- | --- |
| `agent/conversation_loop.py` — call `maybe_capture_turn_feedback(...)` after the final response | **`post_llm_call` plugin hook.** Fires once per turn, **only when `final_response and not interrupted`**, and already passes `user_message=original_user_message` (the *original* message, not memory-injected text — exactly what §7 requires), `assistant_response`, `conversation_history`, `session_id`, `model`, `platform`. | `agent/conversation_loop.py:4566-4583` |
| `hermes_cli/main.py` — register `hermes memory …` subcommands | **`ctx.register_cli_command(...)`.** The CLI auto-discovers plugin commands and builds the argparse subparser — no edit to `main.py`. Becomes a top-level `hermes compiled-memory …` group. | `hermes_cli/plugins.py:386` (registration) → `hermes_cli/main.py:13231` (auto-wiring) |
| `hermes_cli/config.py` — add a `compiled_memory` config block | **`plugins.entries.<plugin_id>.*` config namespace** (read via `cfg_get`), already supported for plugins. No edit to `config.py`. | `agent/plugin_llm.py:203-224` |

Net result: **Files to modify upstream = 0.** The only out-of-plugin change is a
one-line opt-in in the user's *own* `config.yaml` (`plugins.enabled: [...]`,
written by `hermes plugins enable compiled-memory`) — user config, not a tracked
source file, so it never conflicts.

### Why bundled (in-fork) and not a user plugin

We ship at `plugins/compiled-memory/` committed to `agent` (the private fork). It is its
own namespaced directory, so an upstream merge only conflicts in the impossible
case that NousResearch ships a plugin with the identical name. It stays in the
fork's version control and runs in the repo's pytest/CI (this plan is heavily
TDD-driven — see §0/§17 — so CI coverage matters). A `~/.hermes/plugins/` user
plugin would have *zero* repo surface but would fall outside version control and
CI and be gated as "untrusted"; rejected for those reasons.

### Plugin mechanics to respect

- **Manifest** `plugins/compiled-memory/plugin.yaml` must set `kind: standalone`
  **explicitly**. The loader auto-coerces a plugin to `kind: exclusive`
  (memory-provider category) if its `__init__.py` mentions `MemoryProvider` /
  `register_memory_provider` *and* `kind` is absent from the manifest
  (`hermes_cli/plugins.py:1311-1331`). Setting `kind` explicitly disables that
  heuristic. Declare `hooks: [post_llm_call]`.
- **`register(ctx)`** in `__init__.py` is the single entry point. It wires the
  hook, the CLI command, and (optionally) a slash command — mirror the
  `plugins/disk-cleanup/__init__.py` pattern:
  ```python
  def register(ctx) -> None:
      ctx.register_hook("post_llm_call", _on_post_llm_call)
      ctx.register_cli_command(
          "compiled-memory", help="Compiled-memory compiler/linter for Hermes memory surfaces",
          setup_fn=_setup_cli, handler_fn=None,  # subcommands set their own func=
      )
      # optional, later: ctx.register_command("compiled-memory", handler=_handle_slash, ...)
  ```
- **One-external-provider limit does _not_ apply.** This is deliberately *not* a
  `MemoryProvider` (those are capped at one external provider, conflicting with
  Honcho/Mem0/etc.). Using the `post_llm_call` hook sidesteps that cap entirely
  and coexists with any active memory provider.
- **Hook safety is already enforced.** `invoke_hook` wraps every callback in its
  own try/except (`hermes_cli/plugins.py:1556-1567`), so a raised exception in
  capture can never break the turn — but still wrap internally per §20.

### What stays exactly as designed

The storage layout (§4, under `get_hermes_home()`), all Pydantic schemas (§6), the
classifier/recurrence/compiler/linter/eval logic (§7–§13), and every guardrail
(§20) are unchanged. Only the package path and the three integration seams move.

### Account-usage plugin pilot findings to carry forward

The smaller `plugins/account_usage/` pilot validated the plugin-first migration
path and surfaced several implementation details that should shape compiled
memory:

1. **Plan the final consumer seam before extracting code.** A plugin CLI is not
   enough if live runtime paths still import a core module. For compiled memory,
   keep runtime integration hook-driven (`post_llm_call`) and CLI-driven only;
   avoid designing a feature that later requires core modules to import the
   plugin as a service.
2. **Use temporary compatibility shims only as a migration step.** The account
   usage migration first kept `agent/account_usage.py` as a shim, rewired
   gateway/CLI callers to `plugins.account_usage.usage`, then restored the old
   core file to an exact `origin/main` mirror. Compiled memory should not leave
   permanent shims in `agent/` or `hermes_cli/`.
3. **Patch tests to target the plugin module path.** Any monkeypatch target that
   previously pointed at core must move to the plugin module. This caught hidden
   dependency direction quickly in the pilot.
4. **Verify both feature tests and merge cleanliness.** Use targeted pytest for
   behavior, then verify retired core paths with `git diff --quiet origin/main --
   <path>`. `git status` alone can still show a path modified relative to a
   private-fork branch even when the working copy is byte-for-byte upstream.
5. **Prefer importable directory names for direct test ergonomics.**
   `plugins/account_usage/` was easy to import directly in tests. If compiled
   memory keeps the manifest/display name `compiled-memory`, make sure the test
   harness imports via the plugin loader or clearly maps the on-disk slug to the
   Python module name (`compiled_memory`).
6. **Keep plugin registration thin.** `register(ctx)` should wire hooks/CLI and
   delegate all behavior to small modules. This made the pilot easy to test with
   a fake `PluginContext` and should be repeated here.
7. **Document cleanup in the manual as part of acceptance.** The pilot was not
   considered complete until the active-customizations manual reflected the
   pluginized ownership and removed retired core files from the changed-file
   list.

## 3. Current repo anchors

**Integration seams (the plugin wires into these — does NOT edit them):**

- `hermes_cli/plugins.py` — the plugin system. `register(ctx)` entry point;
  `ctx.register_hook`, `ctx.register_cli_command`, `ctx.register_command`;
  `invoke_hook` per-callback try/except isolation; `plugins.entries.<id>` config.
- `agent/conversation_loop.py:4566-4583` — **`post_llm_call` hook fire site**
  (read-only anchor). This is the feedback-capture seam; we register a callback,
  we do not edit this file.
- `hermes_cli/main.py:13212-13244` — auto-wires plugin `register_cli_command`
  entries into argparse (read-only anchor).
- `plugins/disk-cleanup/` — reference implementation of a standalone bundled
  plugin (manifest + `register()` + `post_tool_call`/`on_session_end` hooks +
  slash command). Mirror its shape.

**Durable surfaces the compiler routes to (read/propose, never auto-mutate):**

- `tools/memory_tool.py`
  - Profile-scoped `MEMORY.md` and `USER.md` storage under `get_hermes_home() / "memories"`.
  - Frozen memory snapshot is injected at session start.
  - Uses `§` delimiters and injection/threat scanning.
- `agent/memory_provider.py` / `agent/memory_manager.py`
  - Provider lifecycle + one-external-provider cap. Noted here only to explain
    why we use `post_llm_call` instead of becoming a provider (see §2.5).
- `tools/session_search_tool.py` and `hermes_state.py`
  - Existing session DB / FTS infrastructure useful for recurrence detection.

**Invoked by cron, not edited:**

- `hermes_cli/cron.py` — existing cron invokes the plugin's CLI command
  (`hermes compiled-memory compile --write-briefing --quiet`); no edit to cron.

Existing test areas to protect:

- `tests/agent/test_memory_provider.py`
- `tests/tools/test_memory_tool.py`
- `tests/tools/test_session_search.py`
- `tests/run_agent/test_memory_sync_interrupted.py`
- `tests/run_agent/test_memory_provider_init.py`
- `tests/run_agent/test_commit_memory_session_context_engine.py`
- `tests/hermes_cli/test_memory_reset.py`
- `tests/cron/*`

## 4. Proposed storage layout

All new storage must be profile-scoped via dynamic `get_hermes_home()` resolution.

```text
<get_hermes_home()>/
  compiled_memory/
    registry.yaml
    feedback/
      events.jsonl
    consolidation/
      log.jsonl
      briefings/
        latest.json
        latest.md
    lint/
      latest.json
      reports/
        <timestamp>.json
    evals/
      memory_adherence.jsonl
      skill_regression.jsonl
      reporting.jsonl
      content_agent.jsonl
      routing.jsonl
```

Rules:

- Never use `Path.home() / ".hermes"`.
- Import `from hermes_constants import get_hermes_home` (the canonical resolver used by `tools/memory_tool.py`, `disk-cleanup`, and `plugins.py`).
- Never cache `HERMES_HOME` at import time.
- Create directories lazily on write, not on import.
- Tests must redirect `HERMES_HOME` to temp dirs.

### Source-path convention (revised packaging)

All source modules live **inside the plugin** under `plugins/compiled-memory/`.
They are plain modules imported relatively from the plugin's `register()`
(`from . import compiler, feedback, ...`), loaded by the plugin loader as
`hermes_plugins.compiled_memory` (the loader slugs `-` → `_`). The runtime
**storage** layout above (under `get_hermes_home()/compiled_memory/`) is
unchanged. Tests move from `tests/plugins/compiled-memory/` to
`tests/plugins/compiled_memory/` (see §18). All later phases use the plugin paths directly; do not reintroduce `agent/compiled_memory/`.

## 5. Phase 0 — Baseline and test harness

### Objective

Create the test shell before implementation so compiled-memory code stays profile-safe and non-mutating.

### Add tests

- `tests/plugins/compiled_memory/test_paths.py`
  - verifies storage resolves under active `HERMES_HOME`.
  - verifies no writes leak into default profile when `HERMES_HOME` is redirected.
- `tests/plugins/compiled_memory/test_models.py`
  - validates model schemas and enum rejection.
  - verifies JSON round-trip.
- `tests/plugins/compiled_memory/test_store.py`
  - validates JSONL append/read/status-update behavior.
  - verifies corrupt lines are reported/skipped without crashing.
- `tests/plugins/compiled_memory/test_register.py` (added once `register()` is real)
  - the manifest parses as `kind: standalone` (not auto-coerced to `exclusive`).
  - `register(ctx)` registers a `post_llm_call` hook and a `compiled-memory` CLI command — using a fake/stub `ctx` so it needs no full agent boot.
- `tests/plugins/compiled_memory/test_hook_contract.py`
  - verifies `hermes_cli.plugins.VALID_HOOKS` still contains `post_llm_call`.
  - verifies `agent/conversation_loop.py` still invokes `invoke_hook("post_llm_call", ...)` with at least `user_message`, `assistant_response`, `conversation_history`, `session_id`, `model`, and `platform`.
  - purpose: catch future upstream hook removal/rename even if the plugin's direct callback tests still pass.

### Verification

```bash
pytest tests/plugins/compiled_memory -q
```

## 6. Phase 1 — Plugin scaffold, core package and schemas

### Add plugin scaffold + package

```text
plugins/compiled-memory/plugin.yaml     # name: compiled-memory; kind: standalone; hooks: [post_llm_call]
plugins/compiled-memory/__init__.py     # register(ctx) — starts as a no-op stub; hook/CLI wired in Phase 2/6
plugins/compiled-memory/models.py
plugins/compiled-memory/paths.py
plugins/compiled-memory/store.py
plugins/compiled-memory/registry.py
```

`plugin.yaml` (pin `kind` explicitly — see §2.5):

```yaml
name: compiled-memory
version: 0.1.0
kind: standalone
description: "Compiled-memory compiler/linter — captures raw feedback signals and routes verified conclusions to durable memory surfaces. Non-mutating in v1."
hooks:
  - post_llm_call
```

`__init__.py` exposes `register(ctx)` (wiring filled in by later phases):

```python
def register(ctx) -> None:
    from . import runtime_hooks, cli   # local imports keep load cheap
    ctx.register_hook("post_llm_call", runtime_hooks.on_post_llm_call)   # Phase 2
    ctx.register_cli_command(                                            # Phase 6
        "compiled-memory",
        help="Compiler/linter for Hermes memory surfaces",
        setup_fn=cli.setup,
    )
```

Enable in dev with `hermes plugins enable compiled-memory` (or add to
`plugins.enabled`). The general loader gates user-visible standalone plugins on
that allow-list. After enabling in a gateway deployment, restart the gateway/agent
process so the plugin is discovered and the `post_llm_call` hook is active.

Operational verification after enabling:

```bash
hermes plugins list | grep compiled-memory
hermes compiled-memory --help
```

### `paths.py`

Implement dynamic path helpers:

- `compiled_memory_dir() -> Path`
- `registry_path() -> Path`
- `feedback_events_path() -> Path`
- `consolidation_log_path() -> Path`
- `briefings_dir() -> Path`
- `lint_dir() -> Path`
- `evals_dir() -> Path`

### `models.py`

Use Pydantic v2 models.

Enums:

- `FeedbackType`
  - `preference`
  - `fact`
  - `workflow`
  - `safety`
  - `quality`
  - `tool`
  - `routing`
- `FeedbackStatus`
  - `captured`
  - `compiled`
  - `verified`
  - `escalated`
  - `suppressed`
- `CompiledSurfaceKind`
  - `hot_memory`
  - `user_profile`
  - `skill`
  - `reference`
  - `obsidian_note`
  - `plan_doc`
  - `domain_wiki`
  - `eval_dataset`
  - `suppressed`
- `WritePolicy`
  - `manual`
  - `compiler_proposal`
  - `compiler_verified`
  - `agent_tool`
  - `readonly`
- `AuthorityLevel`
  - `user_explicit`
  - `repo_doc`
  - `tool_output`
  - `session_observation`
  - `web_source`
  - `derived`

Models:

- `FeedbackEvent`
  - `event_id`
  - `timestamp`
  - `profile`
  - `session_id`
  - `response_ref`
  - `type`
  - `severity`
  - `raw_feedback`
  - `proposed_destination`
  - `status`
  - `source`
  - `metadata`
- `CompiledMemorySurface`
- `CompiledMemoryRegistry`
- `RouteDecision`
- `ConsolidationLogEntry`
- `LintFinding`
- `RecurrencePattern`
- `EvalCase`

### `store.py`

Implement reusable storage primitives:

- `append_jsonl(path: Path, model_or_dict) -> None`
- `read_jsonl(path: Path, model_cls=None, limit=None) -> list`
- `update_jsonl_status(path: Path, event_id: str, status: str) -> bool`
- `write_json_atomic(path: Path, data: Any) -> None`
- `read_json_or_default(path: Path, default: Any) -> Any`

For JSONL status update, rewrite via temp file then atomic replace.

Concurrency requirements:

- All JSONL append/rewrite operations must be guarded by an advisory lock file
  next to the store, e.g. `events.jsonl.lock`.
- Use Hermes' existing cross-platform pattern from `tools/memory_tool.py` /
  `tools/skill_usage.py`: `fcntl.flock` on Unix/macOS, `msvcrt.locking` on
  Windows, and a small helper/context manager inside `store.py`.
- Hold the lock for `append_jsonl`, `update_jsonl_status`, and any read-modify-write
  sequence that rewrites a JSON/JSONL file.
- Tests should simulate two writers enough to prove no dropped line/status update
  under serialized access.

### `registry.py`

Implement:

- `load_registry() -> CompiledMemoryRegistry`
- `ensure_default_registry() -> CompiledMemoryRegistry`
- `save_registry(registry) -> None`
- `route_feedback_event(event, registry) -> RouteDecision`

Default registry surfaces:

- `hot_memory`
  - path: `memories/MEMORY.md`
  - owner: `main_hermes`
  - write policy: `compiler_proposal`
- `user_profile`
  - path: `memories/USER.md`
  - owner: `main_hermes`
  - authority: `user_explicit`
  - write policy: `compiler_proposal`
- `skills`
  - path: `skills/`
  - owner: `skill_curator`
  - write policy: `compiler_proposal`
- `feedback_events`
  - path: `compiled_memory/feedback/events.jsonl`
  - owner: `compiled_memory`
  - write policy: `agent_tool`
- `evals`
  - path: `compiled_memory/evals/`
  - owner: `compiled_memory`
  - write policy: `compiler_verified`

Default routing:

- `preference` -> `user_profile`
- `fact` -> `hot_memory` or `user_profile`, depending subject.
- `workflow` -> `skills` or `hot_memory`.
- `tool` -> `skills` or `hot_memory`.
- `routing` -> `evals/routing`.
- `quality` -> `evals/reporting`, `evals/content_agent`, or `feedback_events`, depending metadata.
- `safety` -> `hot_memory` or `skills`, with verification required.

## 7. Phase 2 — Feedback capture service

### Add

```text
plugins/compiled-memory/feedback.py
plugins/compiled-memory/classifier.py
plugins/compiled-memory/redaction.py        # capture-time secret/credential minimization
plugins/compiled-memory/runtime_hooks.py   # on_post_llm_call — the capture seam
```

### `feedback.py`

Public API:

- `capture_feedback_event(...) -> FeedbackEvent`
- `list_feedback_events(status=None, type=None, limit=100) -> list[FeedbackEvent]`
- `mark_feedback_status(event_id, status) -> bool`
- `feedback_summary(limit=50) -> dict`

Rules:

- Assign UUID event IDs.
- Use ISO timestamps.
- Derive active profile safely.
- Redact/minimize before persistence: apply capture-time secret redaction to
  `raw_feedback` before writing it to JSONL; mark `metadata.redacted = true`
  when any replacement happens.
- Store only the user feedback/correction snippet and minimal metadata; never
  persist full assistant responses by default.
- Append only to `compiled_memory/feedback/events.jsonl`.
- Do not write to `MEMORY.md` or `USER.md`.

### `classifier.py`

Implement lightweight heuristic capture, no model call.

Capture likely corrections/feedback, not every user message.

Initial heuristics:

- `preference`
  - “I prefer...”
  - “don’t call me...”
  - “always...” / “never...”
  - “stop doing...”
- `fact`
  - “that’s wrong”
  - “actually...”
  - “correction:”
  - “you got X wrong”
- `workflow`
  - “next time use...”
  - “you should have run...”
  - “don’t ask, just...”
- `tool`
  - “tool failed”
  - “you should have used...”
  - repeated command/test failure language
- `routing`
  - “use Codex/DeepSeek/Flash/Pro”
  - “wrong model”
- `quality`
  - “too verbose”
  - “not concise”
  - “hallucinated”
- `safety`
  - “unsafe”
  - “don’t expose...”
  - “secret”

### `runtime_hooks.py`

This module is the **`post_llm_call` hook callback** registered in
`register(ctx)`. Its signature must accept the hook's kwargs verbatim
(`hermes_cli.invoke_hook` calls `cb(**kwargs)`), so accept `**_` to stay
forward-compatible if upstream adds kwargs later:

```python
def on_post_llm_call(
    *,
    user_message: Any = None,
    assistant_response: Any = None,
    session_id: str | None = None,
    conversation_history: list[dict[str, Any]] | None = None,
    model: str | None = None,
    platform: str | None = None,
    **_,                      # tolerate future kwargs without breaking
) -> None:
    """Registered via ctx.register_hook('post_llm_call', on_post_llm_call)."""
    ...
```

The hook fire site (`agent/conversation_loop.py:4573`) already guarantees the
two preconditions the original plan enforced manually:

- fires **only when `final_response and not interrupted`** → interrupted turns
  capture nothing, for free.
- passes `user_message=original_user_message` → the **original** user text, not
  memory-injected context. (Matches the §20 "use original user message" rule.)

Rules (unchanged):

- Best-effort only; gated by `plugins.entries.compiled-memory.feedback_capture` (default on).
- Wrap all failures; never block user response. (`invoke_hook` also isolates per-callback.)
- Skip non-string/multimodal input.
- Store `response_ref`, not full assistant response.
- Avoid capturing normal first-turn task requests unless a correction pattern matches.

### Integration (no core-file edit)

Wire the callback in the plugin's `register(ctx)`:

```python
ctx.register_hook("post_llm_call", on_post_llm_call)
```

That is the entire integration — `agent/conversation_loop.py` is **not modified**.
Because `post_llm_call` fires for the shared `run_conversation` turn loop, this
covers both CLI and gateway (Telegram/Discord/etc.) surfaces.

Response reference v1 is **best-effort**, not a stable database pointer. Avoid
promising a precise turn ID until the hook receives a real message/turn ID from
core. Store:

```text
session_id:<session_id>
timestamp:<event_timestamp>
turn_index_best_effort:<derived from conversation_history length, if available>
user_message_hash:<short hash of normalized user_message>
```

### Tests

- `tests/plugins/compiled_memory/test_feedback_classifier.py`
- `tests/plugins/compiled_memory/test_runtime_hook.py` — call `on_post_llm_call(**kwargs)` directly with hook-shaped kwargs (no full agent boot needed).

Assertions:

- correction-like user turn writes exactly one event.
- normal task request writes no event.
- the hook isn't invoked on interrupted turns (fire site is gated) — and if called defensively with empty/None args, it writes no event.
- capture-time redaction replaces obvious secrets/tokens before JSONL write and sets `metadata.redacted = true`.
- a raised exception inside the callback is swallowed and never propagates (mirrors `invoke_hook` isolation).
- hook-contract test fails clearly if upstream removes/renames `post_llm_call` or stops passing the required kwargs.

## 8. Phase 3 — Recurrence detector

### Add

```text
plugins/compiled-memory/recurrence.py
```

### Implement

- `detect_recurrences(events: list[FeedbackEvent], *, min_count=2) -> list[RecurrencePattern]`
- `load_and_detect_recurrences(...) -> list[RecurrencePattern]`

Pattern kinds:

- `repeated_correction`
- `failed_tool_pattern`
- `missing_context_lookup`
- `preference_violation`
- `stale_report_caveat`
- `routing_mismatch`
- `quality_regression`

Heuristics:

- same normalized feedback text appears at least `N` times.
- same `type` plus similar content tokens appears at least `N` times.
- “again”, “still”, “I told you”, “as I said” boost severity.
- “you should have searched/read/looked up” maps to missing context lookup.
- stale report caveat keywords: “still says”, “out of date”, “old report”, “stale”.

Suggested destinations:

- repeated preference -> `user_profile`
- failed tool pattern -> `skills` or `hot_memory`
- routing mismatch -> `evals/routing`
- stale report caveat -> `evals/reporting` or docs/plan correction

### Tests

- `tests/plugins/compiled-memory/test_recurrence.py`

## 9. Phase 4 — Compiler briefing and consolidation contract

### Add

```text
plugins/compiled-memory/compiler.py
plugins/compiled-memory/briefing.py
```

### `compiler.py`

Implement a non-mutating compiler.

Functions:

- `compile_feedback_events(...) -> CompilerResult`
- `build_route_decisions(events, registry) -> list[RouteDecision]`
- `write_consolidation_log(entry) -> None`

V1 behavior:

- Propose routes.
- Log decisions.
- Mark verification requirements.
- Suppress low-confidence/noisy duplicates.
- Do not directly mutate `MEMORY.md`, `USER.md`, skills, or docs.

### `briefing.py`

Generate machine-readable and human-readable briefings for sleep consolidation.

Functions:

- `generate_compiled_memory_briefing(...) -> dict`
- `render_briefing_markdown(briefing: dict) -> str`
- `write_latest_briefing() -> tuple[Path, Path]`

Briefing must include:

- recent captured feedback events.
- recurrence patterns.
- proposed route decisions.
- required verification checks.
- suggested eval cases.
- warning: do not directly write raw feedback into durable memory.

Outputs:

```text
compiled_memory/consolidation/briefings/latest.json
compiled_memory/consolidation/briefings/latest.md
```

### Tests

- `tests/plugins/compiled-memory/test_compiler.py`
- `tests/plugins/compiled-memory/test_briefing.py`

## 10. Phase 5 — Linter

### Add

```text
plugins/compiled-memory/linter.py
```

### Lint scopes

- `memories/MEMORY.md`
- `memories/USER.md`
- `compiled_memory/registry.yaml`
- `compiled_memory/feedback/events.jsonl`
- `skills/` metadata/readmes where cheap and safe

### Rules

- `memory_entry_too_long`
- `duplicate_memory_entry`
- `conflicting_user_preference`
- `stale_temporal_claim`
- `missing_authority`
- `unverified_feedback_promoted`
- `registry_path_outside_profile`
- `disabled_surface_routed`
- `skill_without_trigger_or_scope`
- `eval_missing_expected_behavior`

### Functions

- `lint_registry() -> list[LintFinding]`
- `lint_memory_files() -> list[LintFinding]`
- `lint_feedback_store() -> list[LintFinding]`
- `run_lint(scope="all") -> list[LintFinding]`
- `write_lint_report(findings) -> Path`

Reports:

```text
compiled_memory/lint/latest.json
compiled_memory/lint/reports/<timestamp>.json
```

### Tests

- `tests/plugins/compiled-memory/test_linter.py`

## 11. Phase 6 — CLI integration (no `main.py` edit)

### Add

```text
plugins/compiled-memory/cli.py     # setup_fn(subparser) + per-subcommand handlers
```

### Wire via the plugin context (NOT by editing main.py)

In `register(ctx)`:

```python
from . import cli as _cli
ctx.register_cli_command(
    "compiled-memory",
    help="Compiler/linter for Hermes memory surfaces",
    setup_fn=_cli.setup,        # receives the argparse subparser; adds sub-subparsers
)
```

`main.py` already discovers and mounts plugin CLI commands
(`hermes_cli/main.py:13231`), so this appears as a real `hermes compiled-memory …`
command with no core edit. Each sub-subparser sets its own handler via
`set_defaults(func=...)` inside `setup()`.

> **Naming change:** subcommands move from `hermes memory <x>` to a top-level
> `hermes compiled-memory <x>`. `register_cli_command` only registers top-level
> `hermes <name>` commands; nesting under the built-in `hermes memory` group
> would require editing `main.py` (the thing we're avoiding). The top-level group
> is also cleaner — it keeps the built-in `hermes memory setup/status/off/reset`
> surface untouched and conflict-free.

```bash
hermes compiled-memory compile [--dry-run] [--write-briefing] [--limit N] [--quiet]
hermes compiled-memory lint [--scope all|registry|memory|feedback|skills] [--json]
hermes compiled-memory feedback list [--status captured] [--type preference] [--limit N]
hermes compiled-memory feedback mark <event_id> <status>
hermes compiled-memory registry show
hermes compiled-memory registry init
```

Implementation rules:

- Built-in `hermes memory setup/status/off/reset` is **never touched** (different command tree).
- `compile` defaults to dry-run/proposal-only behavior.
- `--write-briefing` writes latest briefing files.
- `--quiet` emits no stdout on success for cron compatibility.

### Tests

- `tests/plugins/compiled_memory/test_cli.py`
- Existing `tests/hermes_cli/test_memory_reset.py` must still pass (we don't touch that command tree).

## 12. Phase 7 — Sleep consolidation / cron bridge

### Goal

Make existing sleep consolidation consume compiler briefings rather than raw, unstructured session memories.

### Add

```text
plugins/compiled-memory/sleep_bridge.py
```

### Implement

- `build_sleep_consolidation_context() -> str`
- `write_sleep_briefing_for_cron() -> Path`

Cron entry point — the **plugin CLI command** (no `cron.py` edit, no `python -m`
target since `plugins/compiled-memory/` is not an importable top-level package):

```bash
hermes compiled-memory compile --write-briefing --quiet
```

Cron rules:

- No stdout on success in quiet mode.
- No direct durable-memory mutation.
- Writes only briefing/log files under active profile.
- Existing sleep consolidation prompt can read the latest briefing and then use existing `memory`/`skill_manage`/docs tools to promote verified items.
- The user registers this cron line via existing `hermes cron` tooling — a config/schedule action, not a source edit.

### Operator wiring checklist

Source remains merge-safe, but deployment requires explicit runtime wiring:

1. Enable the plugin: `hermes plugins enable compiled-memory`.
2. Restart the gateway/agent process that serves Telegram/other long-lived sessions.
3. Verify `hermes compiled-memory --help` works.
4. Create/update a cron job that runs `hermes compiled-memory compile --write-briefing --quiet`.
5. Update the sleep-consolidation cron prompt/instructions to read
   `compiled_memory/consolidation/briefings/latest.md` before deciding whether to
   promote anything into memory/skills/docs.
6. Verify quiet success: a clean compile emits no stdout and does not send Telegram noise.

### Tests

- `tests/plugins/compiled_memory/test_cron_silent.py`

## 13. Phase 8 — Eval/regression generation

### Add

```text
plugins/compiled-memory/evals.py
```

### Eval classes

- `memory_adherence`
- `skill_regression`
- `reporting`
- `content_agent`
- `routing`

### `EvalCase` fields

- `case_id`
- `class_name`
- `source_event_ids`
- `prompt`
- `expected_behavior`
- `forbidden_behavior`
- `surface_refs`
- `created_at`
- `status`

### Functions

- `generate_eval_cases(events, recurrences) -> list[EvalCase]`
- `append_eval_cases(cases, class_name=None) -> None`
- `dedupe_eval_cases(...)`

Mapping:

- preference corrections -> `memory_adherence`
- tool failure recurrence -> `skill_regression`
- stale report caveat -> `reporting`
- Zoe/content-agent scoped corrections -> `content_agent`
- wrong model/tool routing -> `routing`

### Tests

- `tests/plugins/compiled-memory/test_evals.py`

## 14. Phase 9 — Optional model-facing tool

Do **not** implement until internal APIs are stable.

When added, register it from inside the plugin via `ctx.register_tool(...)` (lands
in the global tool registry alongside built-ins) — **not** by dropping a file in
`tools/` or editing `toolsets.py`/`model_tools.py`:

```text
plugins/compiled-memory/tool.py    # schema + handler, wired via ctx.register_tool()
```

Initial read-only actions:

- `feedback`
- `briefing`
- `lint`
- `registry_read`

Avoid mutation actions until the compiler/linter is proven safe.

## 15. Phase 10 — Optional bounded domain wiki

Standalone LLM Wiki remains opt-in and domain-scoped.

### Config (no `config.py` edit — plugin config namespace)

Config lives under the plugin's own namespace in the **user's** `config.yaml`,
read with `cfg_get(load_config(), "plugins", "entries", "compiled-memory", ...)`.
The plugin supplies defaults in code (fail-safe if the key is absent), so no
schema needs to land in `hermes_cli/config.py`:

```yaml
# ~/.hermes/config.yaml  (user config, not a tracked source file)
plugins:
  enabled:
    - compiled-memory          # written by `hermes plugins enable compiled-memory`
  entries:
    compiled-memory:
      enabled: true
      feedback_capture: true   # gates the post_llm_call capture
      auto_compile: false
      domain_wikis: []
      lint:
        monthly: true
```

Rules:

- A domain wiki must declare scope, owner, path, authority, source rules, and write policy.
- Paths outside the active profile require explicit config/allowance.
- No autonomous wiki writes in v1.

## 16. Recommended implementation order

0. **Plugin scaffold** — `plugin.yaml` (`kind: standalone`) + `register()` stub; confirm it loads (`hermes plugins list`) and a `test_register.py` passes. Establish the merge-safe shell before any logic.
1. Core schemas/path/store.
2. Default registry and routing.
3. Feedback capture API and classifier.
4. **`post_llm_call` hook callback** (replaces the old conversation-loop edit).
5. Recurrence detector.
6. Compiler and briefing generation.
7. Linter.
8. CLI subcommands via `register_cli_command` (top-level `hermes compiled-memory`).
9. Cron/sleep bridge (cron invokes the plugin CLI command).
10. Eval generation.
11. Optional read-only model-facing tool via `ctx.register_tool`.
12. Optional bounded domain wiki integration.

## 17. TDD checklist

Run narrow tests after each phase, then broad memory/context regression.

```bash
pytest tests/plugins/compiled_memory -q
pytest tests/tools/test_memory_tool.py tests/agent/test_memory_provider.py -q
pytest tests/run_agent/test_memory_sync_interrupted.py tests/run_agent/test_commit_memory_session_context_engine.py -q
```

Broader regression (confirm we left the memory/CLI surfaces untouched):

```bash
pytest tests/agent tests/tools/test_memory_tool.py tests/run_agent tests/hermes_cli/test_memory_reset.py tests/cron tests/plugins/compiled_memory -q
```

Before starting the feature branch, record the current private-fork base so the
feature's own diff is not confused with pre-existing fork deltas:

```bash
BASE=$(git rev-parse HEAD)
git checkout -b compiled-memory-architecture-v1
```

Before PR:

```bash
git diff --check
git diff --name-only "$BASE"...HEAD   # expect ONLY plugins/compiled-memory/** and tests/plugins/compiled_memory/**
pytest tests/plugins/compiled_memory -q
```

**After every `git merge origin/main`** (the recurring future-proofing check):

```bash
git fetch origin main
git merge origin/main          # should never conflict in compiled-memory core files
pytest tests/plugins/compiled_memory -q   # green ⇒ hook/CLI contracts still hold
```

## 18. Files to add

All new files live in **one self-contained directory** plus its tests. Nothing is
added to `agent/`, `hermes_cli/`, or `tools/`.

```text
plugins/compiled-memory/plugin.yaml        # name, version, kind: standalone, hooks: [post_llm_call]
plugins/compiled-memory/__init__.py        # register(ctx): hook + CLI command (+ optional slash cmd)
plugins/compiled-memory/models.py
plugins/compiled-memory/paths.py
plugins/compiled-memory/store.py
plugins/compiled-memory/registry.py
plugins/compiled-memory/feedback.py
plugins/compiled-memory/classifier.py
plugins/compiled-memory/redaction.py
plugins/compiled-memory/runtime_hooks.py   # on_post_llm_call callback (the capture seam)
plugins/compiled-memory/recurrence.py
plugins/compiled-memory/compiler.py
plugins/compiled-memory/briefing.py
plugins/compiled-memory/linter.py
plugins/compiled-memory/evals.py
plugins/compiled-memory/sleep_bridge.py
plugins/compiled-memory/cli.py             # setup_fn(subparser) + subcommand handlers
plugins/compiled-memory/README.md

tests/plugins/compiled_memory/__init__.py
tests/plugins/compiled_memory/test_models.py
tests/plugins/compiled_memory/test_paths.py
tests/plugins/compiled_memory/test_store.py
tests/plugins/compiled_memory/test_registry.py
tests/plugins/compiled_memory/test_feedback_classifier.py
tests/plugins/compiled_memory/test_redaction.py
tests/plugins/compiled_memory/test_runtime_hook.py     # post_llm_call callback: captures corrections, skips normal/interrupted, never raises
tests/plugins/compiled_memory/test_recurrence.py
tests/plugins/compiled_memory/test_compiler.py
tests/plugins/compiled_memory/test_briefing.py
tests/plugins/compiled_memory/test_linter.py
tests/plugins/compiled_memory/test_evals.py
tests/plugins/compiled_memory/test_cli.py
tests/plugins/compiled_memory/test_register.py         # register(ctx) wires exactly the expected hook + CLI command
tests/plugins/compiled_memory/test_hook_contract.py   # core seam still exposes/fires post_llm_call with required kwargs
tests/plugins/compiled_memory/test_cron_silent.py      # `hermes compiled-memory compile --write-briefing --quiet` is silent
```

> Optional later (still inside the plugin): `plugins/compiled-memory/tool.py`
> (§14, registered via `ctx.register_tool`).

## 19. Files to modify

**Upstream source files modified: none.** This is the core merge-safety property —
no merge from `origin/main` can conflict with this feature in `conversation_loop.py`,
`main.py`, `config.py`, `cron.py`, `toolsets.py`, or `model_tools.py`, because none
of them are touched.

The only non-plugin changes are to the **user's own** `~/.hermes/config.yaml`
(not a tracked source file), both done through existing tooling rather than hand-editing:

```text
plugins.enabled: [ ..., compiled-memory ]   # via `hermes plugins enable compiled-memory`
cron line: hermes compiled-memory compile … # via existing `hermes cron` tooling
```

## 20. Guardrails

### Merge-safety (upstream updates) — the primary design constraint

- **Never edit an upstream-owned file.** All code lives under
  `plugins/compiled-memory/`. Integration is through `register_hook`,
  `register_cli_command`, `register_command`, `register_tool`, and the
  `plugins.entries` config namespace — never by editing `conversation_loop.py`,
  `main.py`, `config.py`, `cron.py`, or `tools/`.
- **Depend on hook contracts, not internals.** The `on_post_llm_call` callback
  reads only documented hook kwargs and accepts `**_` so an upstream-added kwarg
  can't break it. Don't import private symbols from core modules.
- **Tolerate hook/kwarg drift.** If upstream renames or augments `post_llm_call`,
  the failure is localized (capture silently no-ops) and never breaks a turn.
- **Pre-merge check.** After `git fetch origin main && git merge origin/main`,
  verify "does the plugin still load + its tests pass" — run the §17 plugin
  suite, including `test_hook_contract.py`. A green run proves the merge didn't
  break the extension seams, and (since no core files were touched) the merge
  itself cannot have conflicted with this feature's source files.
- **Manifest pins `kind: standalone`** so upstream changes to memory-provider
  auto-coercion can't silently re-route the plugin (see §2.5).

### Prompt-cache safety

- Do not add compiled-memory briefing to the system prompt by default.
- Do not alter `MemoryStore.load_from_disk()` snapshot format.
- Do not inject feedback events into active system prompt.
- Do not refresh memory snapshot mid-session.

### Profile safety

- Use `get_hermes_home()` everywhere.
- Tests redirect `HERMES_HOME`.
- Registry paths must be validated against the active profile unless explicitly configured otherwise.

### Raw feedback safety

- Raw feedback is signal, not compiled memory.
- Store raw feedback only in `compiled_memory/feedback/events.jsonl`.
- Durable memory promotion requires compile/reconcile/verify.

### Runtime safety

- Feedback capture must be best-effort.
- All capture errors are logged and swallowed.
- Never block response delivery.
- Never break memory-provider sync.

### Privacy/security

- Do not store full assistant responses by default.
- Store best-effort `response_ref` and minimal metadata only.
- Redact obvious secrets at capture time before JSONL persistence.
- Linter still flags possible secrets in already-captured raw feedback as a second line of defense.

## 21. Acceptance criteria

v1 is complete when:

- The plugin loads as `kind: standalone` and registers exactly one `post_llm_call` hook + the `compiled-memory` CLI command (no core files modified by this feature — `git diff --name-only "$BASE"...HEAD` shows only `plugins/compiled-memory/**` + `tests/plugins/compiled_memory/**`).
- `hermes plugins enable compiled-memory` enables the plugin, `hermes plugins list` shows it enabled, and `hermes compiled-memory --help` works after discovery/restart.
- `hermes compiled-memory compile --dry-run` produces route decisions without mutating durable memory.
- `hermes compiled-memory compile --write-briefing --quiet` writes `latest.json` and `latest.md` silently on success.
- feedback-like user corrections are captured into profile-scoped `compiled_memory/feedback/events.jsonl` under file lock.
- obvious secrets/tokens are redacted before capture persistence.
- normal task messages are not captured as feedback.
- recurrence detection identifies repeated corrections and repeated tool/workflow failures.
- linter flags duplicates, stale claims, bad registry paths, corrupt feedback rows, and unverified promotion risks.
- CLI tests and existing memory/context regression tests pass.
- sleep consolidation can consume the briefing and use existing tools to promote verified changes after explicit cron/prompt wiring.
- hook-contract test catches upstream removal/rename of `post_llm_call` or required kwargs.

## 22. First implementation branch suggestion

```bash
BASE=$(git rev-parse HEAD)
git checkout -b compiled-memory-architecture-v1
```

Suggested commit sequence:

1. `compiled-memory: scaffold bundled plugin (manifest + register stub)`
2. `compiled-memory: add profile-scoped models and store`
3. `compiled-memory: add registry and routing`
4. `compiled-memory: capture correction feedback via post_llm_call hook`
5. `compiled-memory: add recurrence detection`
6. `compiled-memory: add non-mutating compiler briefings`
7. `compiled-memory: add lint reports`
8. `compiled-memory: add hermes compiled-memory CLI via register_cli_command`
9. `compiled-memory: quiet briefing bridge for cron`
10. `compiled-memory: generate lightweight eval cases`

Every commit in this feature branch touches **only** `plugins/compiled-memory/**`
and `tests/plugins/compiled_memory/**` — verify against the recorded branch base
with `git diff --name-only "$BASE"...HEAD`. Use `origin/main` only for upstream
merge tests, not for measuring this feature's diff in a private fork that already
has unrelated customizations.

## 23. Open questions before coding

1. Should feedback capture be enabled by default, or gated by `plugins.entries.compiled-memory.feedback_capture`?
   - Recommendation: enabled by default but heuristic-only and non-mutating, with the config kill switch.
2. ~~Should `hermes memory compile` live under `hermes memory`, or become `hermes compiled-memory`?~~ **Resolved (merge-safety):** top-level `hermes compiled-memory`. `register_cli_command` only mounts top-level commands; nesting under `hermes memory` would require editing `main.py`. The top-level group also leaves the built-in `hermes memory` tree untouched.
3. Should v1 include a read-only `compiled_memory` model tool?
   - Recommendation: no. Add CLI/internal API first; expose tool only after behavior is stable.
4. Should LLM Wiki be implemented immediately?
   - Recommendation: no. Treat it as optional bounded-domain surface after registry/linter exists.
5. **Resolved (packaging):** ship as a **bundled** plugin (`plugins/compiled-memory/`) committed to the fork — version-controlled and CI-tested — rather than an out-of-repo `~/.hermes/plugins/` user plugin. Both are merge-safe; bundled wins on testability for a TDD-heavy plan.
6. **Resolved (storage safety):** JSONL writes/status rewrites use advisory lock files; no unlocked read-modify-write storage operations.
7. **Resolved (privacy):** raw feedback is redacted/minimized at capture time before persistence; linter secret detection remains as defense in depth.

## 24. Summary

This plan keeps Hermes’ existing memory architecture intact and adds a compiler layer around it. The important architectural move is to distinguish **raw signal capture** from **compiled durable memory**. Feedback, corrections, and recurring failures become inputs to a profile-scoped compiler/linter pipeline. Only verified, reconciled, routed conclusions should reach hot memory, user profile, skills, docs, or evals.

That gives Hermes a closed-loop memory system without turning every correction into unreviewed prompt bloat.

**On future-proofing:** the whole feature ships as one bundled plugin
(`plugins/compiled-memory/`) that wires in exclusively through Hermes' documented
extension points — the `post_llm_call` hook for capture, `register_cli_command`
for the CLI, `register_tool` for the optional model tool, and the
`plugins.entries` namespace for config. **No upstream-owned file is edited**, so
merging `origin/main` cannot conflict with this work, and the callback's
tolerance of unknown kwargs means upstream hook changes degrade to a silent
no-op rather than a broken turn. Merge-safety is achieved by *addition through
seams*, not by patching hot files.
