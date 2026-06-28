# Adaptive Routing Plugin — Improvement Plan

> **Date:** 2026-06-28 (updated post-merge: 2026-06-28)
> **Status:** Draft — ready for implementation
> **Scope:** Significant improvement to the `adaptive-routing` plugin with manual/auto routing support, smarter scoring, and upstream-merge-safe design
> **Post-merge line numbers:** This plan was written against the pre-merge private-fork tree at `709067b84`. The 2026-06-28 NousResearch upstream integration (commit `0abe7b9a4`, upstream `135f23516`) shifted several symbol locations; corrected line numbers in the table below refer to the **post-merge** branch `update/upstream-2026-06-28`. All architectural intent and ownership boundaries still hold.

---

## Table of Contents

1. [Current State](#1-current-state)
2. [Target Model Stacks](#2-target-model-stacks)
3. [Architecture](#3-architecture)
4. [Proposed Config Schema](#4-proposed-config-schema)
5. [Smarter Scoring Design](#5-smarter-scoring-design)
6. [Quota Integration](#6-quota-integration)
7. [Manual vs Auto Routing](#7-manual-vs-auto-routing)
8. [Phased Implementation Roadmap](#8-phased-implementation-roadmap)
9. [Test Plan](#9-test-plan)

---

## 1. Current State

The plugin is a thin hook-and-CLI wrapper around `agent/reasoning_policy.py`.

### What Exists

| Component | Location (post-merge) | Behavior |
|-----------|----------|----------|
| Route hook | `plugins/adaptive-routing/__init__.py:29` | Calls core `decide_turn_route()`, returns overrides |
| CLI registration | `plugins/adaptive-routing/__init__.py:120` | `ctx.register_cli_command(name="adaptive-routing", ...)` |
| CLI setup/handler | `plugins/adaptive-routing/__init__.py:87` / `:92` | `_route_setup` / `_route_handler` argparsing |
| Task classifier | `agent/reasoning_policy.py:150` | Keyword/length-based scoring (27 hard + 15 medium keywords) |
| Route decision | `agent/reasoning_policy.py:213` | MiMo-first → Codex quota-aware → DeepSeek fallback |
| Codex quota helper | `agent/reasoning_policy.py:68` | `CodexQuotaState` dataclass |
| Provider predicate | `agent/reasoning_policy.py:192` | `is_codex_provider(provider)` |
| Gateway hook fire site | `gateway/run.py:3697` (`_resolve_turn_agent_config`) | Loads policy, fires `resolve_turn_route`, applies route |
| Reasoning override gate | `gateway/run.py:3704` (`force_reasoning_config`) | Per-session reasoning override outranks hook |
| Codex quota cache (60s TTL) | `gateway/run.py:3625` (`_get_codex_quota_state`) | Generic per-provider quota cache lives here post-merge |
| Footer format helper | `gateway/runtime_footer.py:97` / `:108` | `_format_used_percent` / `_codex_quota_used_percent_from_snapshot` |
| Manual model override | `gateway/slash_commands.py:1327` / `:1561` | `self._session_model_overrides[session_key] = {…}` |
| Quota seam | `gateway/quota_service.py` | Codex-only quota snapshots (still the public boundary) |

### Limitations

1. **Keyword-only classification** — no semantic understanding, no context, no multi-turn awareness.
2. **Static model mapping** — hardcoded model names in `DEFAULT_REASONING_POLICY`.
3. **Manual model can be overridden** — no model-lock equivalent to `force_reasoning_config` at `gateway/run.py:3704` (post-merge).
4. **Codex-only quota** — Opencode Go quota not wired.
5. **No feedback loop** — routing decisions are not tracked or improved.
6. **No trace/observability** — minimal diagnostic output.

---

## 2. Target Model Stacks

### Primary Stack (Opencode Go + OpenAI OAuth)

| Tier | Provider | Model | Use Case |
|------|----------|-------|----------|
| Routine | Opencode Go | MiMo 2.5 | Simple, routine tasks |
| Difficult | Opencode Go | Minimax M3 | Heavier, more difficult tasks |
| Complex | OpenAI OAuth | Codex 5.5 | SOTA for complex tasks |

### Fallback Stack #1 (OpenAI OAuth)

| Tier | Provider | Model | Use Case |
|------|----------|-------|----------|
| Routine | OpenAI OAuth | Codex 5.4 Mini | Simple, routine tasks |
| Difficult | OpenAI OAuth | Codex 5.4 | Heavier, more difficult tasks |
| Complex | OpenAI OAuth | Codex 5.5 | SOTA for complex tasks |

### Fallback Stack #3 (DeepSeek PAYG)

| Tier | Provider | Model | Use Case |
|------|----------|-------|----------|
| Routine | DeepSeek PAYG | DeepSeek V4 Flash | Simple, routine tasks |
| Difficult | DeepSeek PAYG | DeepSeek V4 Pro | Heavier, more difficult tasks |
| Complex | DeepSeek PAYG | DeepSeek V4 Pro | SOTA for complex tasks |

### Quota Policy

| Provider | Quota Type | Window | Footer Display |
|----------|-----------|--------|----------------|
| OpenAI OAuth | Rolling usage | 5 hours | ✅ Already wired |
| Opencode Go | Rolling usage | 5 hours | ❌ Needs wiring |
| DeepSeek PAYG | None (direct API) | N/A | ❌ No quota needed |

### Optimization Objective

**Balanced** only — quality versus cost. No latency criteria.

---

## 3. Architecture

### Ownership Principle

- **Plugin-owned** (`plugins/adaptive-routing/`): policy loading, stack definitions, scoring, trace, CLI diagnostics.
- **Core-owned** (`agent/reasoning_policy.py`): pure primitives for classification, route decisions, fallback chains. Stays self-contained — no plugin imports.
- **Gateway-owned** (`gateway/run.py`): route application, quota helpers, session overrides. Small seams only.
- **Quota seam** (`gateway/quota_service.py`): generic provider quota boundary. No hot-path plugin imports.

### Data Flow

```
User message
  → Gateway loads effective policy (plugin config + legacy overlay)
  → Gateway fires resolve_turn_route hook
  → Plugin: extract features → classify tier → score candidates → select route
  → Gateway: apply final decision (skip core routing if plugin returned complete decision)
  → Gateway: resolve runtime provider → build fallback chain
  → Response footer: route label, reasoning effort, quota %
```

### Final Decision Hook Contract

Add a "final decision" return type to the hook contract in `hermes_cli/plugins.py:212` (post-merge location of the `resolve_turn_route` declaration):

- If the plugin returns a complete route decision (provider + model + reasoning_effort + route_label), the gateway applies it directly and skips core `decide_turn_route()`.
- If the plugin returns `None` or an incomplete result, the gateway falls back to core routing.
- This prevents core from overwriting plugin decisions.

---

## 4. Proposed Config Schema

Under `plugins.adaptive_routing` in config:

```yaml
plugins:
  adaptive_routing:
    enabled: true
    mode: auto                    # auto | manual (default route mode)
    objective: balanced           # balanced only

    stacks:
      primary:
        provider: opencode
        routine:
          model: mimo-v2.5
          label: mimo
          quality_score: 0.6
          cost_score: 0.3
          reasoning_range: [low, medium]
        difficult:
          model: minimax-m3
          label: minimax
          quality_score: 0.8
          cost_score: 0.5
          reasoning_range: [medium, high]
        complex:
          provider: openai
          model: codex-5.5
          label: codex
          quality_score: 0.95
          cost_score: 0.8
          reasoning_range: [high, xhigh]

      codex_fallback:
        provider: openai
        routine:
          model: codex-5.4-mini
          label: codex-mini
          quality_score: 0.5
          cost_score: 0.2
          reasoning_range: [low, medium]
        difficult:
          model: codex-5.4
          label: codex
          quality_score: 0.75
          cost_score: 0.6
          reasoning_range: [medium, high]
        complex:
          model: codex-5.5
          label: codex
          quality_score: 0.95
          cost_score: 0.8
          reasoning_range: [high, xhigh]

      deepseek_fallback:
        provider: deepseek
        routine:
          model: deepseek-v4-flash
          label: deepseek-flash
          quality_score: 0.45
          cost_score: 0.15
          reasoning_range: [low, medium]
        difficult:
          model: deepseek-v4-pro
          label: deepseek-pro
          quality_score: 0.7
          cost_score: 0.4
          reasoning_range: [medium, high]
        complex:
          model: deepseek-v4-pro
          label: deepseek-pro
          quality_score: 0.7
          cost_score: 0.4
          reasoning_range: [high, xhigh]

    quotas:
      opencode:
        type: rolling
        window_hours: 5
        low_threshold_percent: 10
        emergency_threshold_percent: 5
        display_in_footer: true
      openai:
        type: rolling
        window_hours: 5
        low_threshold_percent: 4
        emergency_threshold_percent: 2
        display_in_footer: true
      deepseek:
        type: payg
        display_in_footer: false

    scoring:
      quality_weight: 0.7
      cost_weight: 0.3
      quota_penalty_weight: 0.5
      mismatch_penalty_weight: 0.3
      unknown_cost_behavior: neutral  # neutral | penalize | ignore

    manual_routing:
      lock_on_select: true          # manual model selection creates session lock
      clear_with: [route_auto, /new, /reset]

    footer:
      show_route_label: true
      show_reasoning_effort: true
      show_quota_percent: true
      show_score_trace: false       # debug only

    trace:
      enabled: false                # enable for diagnostics
      max_history: 10
```

### Legacy Compatibility

The new plugin-owned config takes precedence. Legacy `agent.reasoning_policy` keys overlay only missing fields, preserving backward compatibility for existing users.

---

## 5. Smarter Scoring Design

### Feature Extraction

Replace keyword-only scoring in `classify_task()` with a richer feature extractor in the plugin:

| Feature | Signal |
|---------|--------|
| Size | Word count, line count, code block count |
| Work type | Implementation, debugging, refactoring, migration, architecture, testing, configuration, explanation, summary, routine |
| Risk | Production, outage, security, data loss, auth, quota, gateway, concurrency |
| Evidence | Traceback, logs, diff, stack traces, failing tests, pasted source |
| Breadth | Multi-file, cross-module, upstream merge, provider/runtime changes |
| Safety | Ambiguous requirements, destructive commands, manual override, degraded quota |

### Complexity Bands

| Band | Characteristics |
|------|----------------|
| Routine | Tiny/easy, single-step, simple questions, low-risk diagnostics |
| Difficult | Medium-to-hard, code changes, debugging, tests, moderate design |
| Complex | Multi-file architecture, gateway/runtime/provider, quota/fallback, production/security/concurrency |

### Balanced Candidate Scoring

Score each candidate model using quality vs cost only:

```
candidate_score = (quality_weight × quality_fit)
                + (cost_weight × normalized_cost)
                + (quota_penalty_weight × quota_penalty)
                + (mismatch_penalty_weight × mismatch_penalty)
```

Where:
- **quality_fit**: tier-to-complexity match + task-kind compatibility + reasoning effort support
- **normalized_cost**: catalog cost when available; configured relative cost when not. Unknown cost uses neutral value.
- **quota_penalty**: 0 when healthy/PAYG; increases as 5-hour window approaches thresholds; hard gate when exhausted
- **mismatch_penalty**: penalize underpowered candidates for complex tasks; penalize overpowered candidates for routine tasks
- **Tie breaker**: stack order → lowest cost → strongest quality

### Reasoning Effort Mapping

| Band | Default | Elevated |
|------|---------|----------|
| Routine | low | medium (if code/log features appear) |
| Difficult | medium | high (implementation/debug/test dominates) |
| Complex | high | xhigh (multi-file, production, security, quota critical) |

---

## 6. Quota Integration

### Generic Quota State

Extend the existing `CodexQuotaState` in `agent/reasoning_policy.py:68` (post-merge) to a provider-agnostic `QuotaState`:

```python
@dataclass(frozen=True)
class QuotaState:
    provider: str
    percent_remaining: Optional[float] = None
    reset_at: Optional[datetime] = None
    unavailable: bool = False
    is_payg: bool = False
```

### Opencode Go Quota Wiring

- Extend `plugins/account_usage/` with an Opencode Go branch that returns the same snapshot shape as Codex.
- Reuse the existing `gateway/quota_service.py` seam — no hot-path plugin imports.
- Fetcher registered during plugin discovery; generic `fetch_quota_snapshot()` dispatches by provider.
- 60-second cache TTL matching existing Codex quota cache at `gateway/run.py:3625` (`_get_codex_quota_state` post-merge).

### DeepSeek PAYG

- `is_payg=True` → no quota evaluation, no footer display.
- Always eligible for routing.

### Footer Display

- Genericize `gateway/runtime_footer.py:97` (`_format_used_percent`) — and the `provider_norm` branch at `:145-168` — to show quota % for both OpenAI OAuth and Opencode Go (post-merge locations).
- DeepSeek footer shows route label only, no quota.

---

## 7. Manual vs Auto Routing

### Problem

Manual model selection (`/model <name>`) stores in `_session_model_overrides` at `gateway/slash_commands.py:1327` and `:1561` (post-merge; the textual picker also writes to the same dict), but adaptive routing can override it because there is no model-lock gate.

### Solution: Explicit Route Mode

| Mode | Behavior |
|------|----------|
| **Manual locked** | Use selected model/provider. Adaptive routing skipped. |
| **Auto adaptive** | Plugin decides per turn. |
| **One-shot manual** | Use selected model for next turn only, then return to auto. |

### Implementation

1. Add `_session_model_lock` dict (analogous to `_session_model_overrides`):
   - Set on `/model <name>` (default = locked).
   - Cleared by `/model auto`, `/routing auto`, `/new`, `/reset`.

2. Add `/routing auto` command:
   - Clears session model lock and session model override.
   - Evicts cached agent.
   - Returns session to adaptive routing.

3. Gateway gate at `_resolve_turn_agent_config()` (`gateway/run.py:3697` post-merge):
   - Check model lock before firing route hook.
   - If locked, skip adaptive routing (analogous to `force_reasoning_config` at `:3704`).

4. Response footer shows route source:
   - `🔒 manual | claude-3.5` — user selected, locked.
   - `⚡ adaptive | mimo | medium` — plugin decided.
   - `🔄 auto-restored` — returned to auto after manual.

---

## 8. Phased Implementation Roadmap

### Phase 1: Plugin Policy Engine

**Files:** `plugins/adaptive-routing/`

- Add plugin-owned config loader with legacy overlay.
- Add stack/tier definitions and validation.
- Add deterministic feature extractor (richer than current keyword-only).
- Add balanced scoring (quality vs cost, no latency).
- Add route trace generation.
- Add CLI diagnostics for new scoring.

**Tests:** `tests/plugins/adaptive_routing/`

- Feature extraction for routine, difficult, complex prompts.
- Balanced scoring with quality and cost only.
- Stack selection and fallback chain derivation.
- Config migration and precedence.

### Phase 2: Final Route Hook Behavior

**Files:** `hermes_cli/plugins.py`, `gateway/run.py`

- Update hook contract for complete final decisions.
- Prevent core `decide_turn_route()` from overwriting plugin decisions.
- Keep fallback to core when plugin is disabled, errors, or returns incomplete result.

**Tests:** `tests/plugins/adaptive_routing/`, `tests/gateway/`

- Final plugin decision not overwritten by core.
- Advisory hook result falls back to core routing.
- Plugin disabled leaves core routing functional.

### Phase 3: Quota Generalization

**Files:** `gateway/quota_service.py`, `gateway/runtime_footer.py`, `plugins/account_usage/`

- Generalize `CodexQuotaState` to provider-agnostic `QuotaState`.
- Extend account-usage plugin with Opencode Go five-hour quota fetching.
- Replace Codex-specific gateway helpers with generic quota-cache helpers.
- Genericize footer quota display for both OpenAI OAuth and Opencode Go.
- Preserve DeepSeek PAYG no-quota behavior.

**Tests:** `tests/plugins/account_usage/`, `tests/gateway/`

- Opencode Go quota snapshot rendering.
- Codex quota behavior unchanged.
- DeepSeek PAYG footer omits quota.
- Quota unavailable degrades gracefully.

### Phase 4: Manual/Auto Routing UX

**Files:** `gateway/slash_commands.py`, `gateway/run.py`

- Add session model lock on manual model selection.
- Add `/routing auto` command to clear lock and return to adaptive.
- Gate adaptive routing behind model lock in `_resolve_turn_agent_config()`.
- Evict cached agents when route mode changes.
- Add route source to footer/diagnostics.

**Tests:** `tests/plugins/adaptive_routing/`, `tests/gateway/`

- Manual model lock prevents adaptive reroute.
- `/routing auto` clears lock and resumes adaptive.
- Route source shown in diagnostics.

### Phase 5: Observability & Hardening

**Files:** `plugins/adaptive-routing/`, `gateway/run.py`

- Add `/routing trace` command showing last N routing decisions.
- Add structured route trace with profile, candidates, scores, chosen tier, quota state, fallback reason.
- Add tests for multi-language, adversarial, edge-case prompts.
- Add performance benchmarks for classification latency.

---

## 9. Test Plan

### Unit Tests (`tests/plugins/adaptive_routing/`, `tests/agent/`)

- [ ] Feature extraction for routine, difficult, complex prompts.
- [ ] Reasoning effort mapping per tier (routine → low/medium, difficult → medium/high, complex → high/xhigh).
- [ ] Balanced scoring with quality and cost only — no latency fields.
- [ ] Cost fallback when catalog cost is missing.
- [ ] Quota penalties for OpenAI OAuth and Opencode Go.
- [ ] DeepSeek PAYG eligibility with no quota penalty.
- [ ] Stack selection: primary → codex fallback → deepseek fallback.
- [ ] Fallback chain derivation excluding selected provider/model.
- [ ] Config migration from legacy `agent.reasoning_policy` to plugin-owned namespace.

### Integration Tests (`tests/gateway/`)

- [ ] Final plugin route decision not overwritten by core `decide_turn_route()`.
- [ ] Advisory/incomplete hook result falls back to core routing.
- [ ] Manual model override prevents adaptive routing from changing provider/model.
- [ ] `/routing auto` clears manual lock and adaptive routing resumes.
- [ ] Generic quota cache used without blocking every turn.
- [ ] Quota service unavailable degrades gracefully.

### Footer & Quota Tests

- [ ] Existing Codex quota footer behavior intact.
- [ ] Opencode Go five-hour quota footer renders when configured.
- [ ] DeepSeek PAYG footer omits quota usage.
- [ ] Route labels and reasoning effort stable for all three stacks.

### Regression Tests

- [ ] Plugin disabled leaves core routing functional.
- [ ] No hot-path imports from plugin modules in `gateway/run.py`.
- [ ] Manual model selection stable across turns until explicit auto return.
- [ ] Legacy config users keep working after migration.

---

## 10. Post-merge state (2026-06-28 NousResearch integration)

The plan's pre-merge line references were refreshed in the table above; everything below here records how the upstream merge interacts with the planned work.

**What changed upstream that this plan must respect:**

- `gateway/run.py` grew from ~10k to 19255 LOC. `_resolve_turn_agent_config` (line 3697) now takes a wider kwargs surface: `policy`, `reasoning_config`, `force_reasoning_config`, `user_message`, `primary_provider`, `primary_model`, `session_key`, `runtime`. The hook fire-site gate is the existing `force_reasoning_config` boolean at line 3704 — a clean analog for the planned model-lock gate.
- `gateway/run.py:3625` introduced a generic `_get_codex_quota_state()` / `_get_codex_quota_used_percent()` pair that already implements a 60-second per-key cache. Phase 3 (Quota Generalization) should **reuse this cache helper directly** rather than introduce a parallel generic quota cache.
- `gateway/runtime_footer.py:97` (`_format_used_percent`) plus the `provider_norm` switch at `:145-168` already dispatches on `{"openai-codex", "codex"}` vs `"deepseek"`. Phase 3's "genericize footer" step reduces to adding an `"opencode"` branch to the same dispatch.
- Upstream added three new kanban-task hook names to `STANDARD_HOOKS` (`kanban_task_claimed`, `kanban_task_completed`, `kanban_task_blocked`). These do not interact with adaptive routing but confirm the hook-list now lives in one place — keep `resolve_turn_route` listed alongside them.

**What this plan adds that upstream does not already cover:**

- Provider-agnostic `QuotaState` in `agent/reasoning_policy.py` (currently `CodexQuotaState` only at line 68).
- Opencode Go five-hour rolling quota in `plugins/account_usage/`.
- Stack/tier definitions, balanced scoring, and route trace (plugin-owned, no upstream equivalent).
- Manual model lock and `/routing auto` UX (no upstream equivalent).
- Final-decision semantics on the `resolve_turn_route` hook contract (the upstream hook is advisory-only today — the gateway still calls `decide_turn_route()` after the hook returns).

**Validation after this merge (recorded for the integration branch `update/upstream-2026-06-28`):**

- All 5 private-fork hook fire sites verified intact in post-merge `gateway/run.py`: `resolve_turn_route` (3736), `pre_gateway_authorize_message` busy (4944) and cold (8255), `format_gateway_runtime_footer` (10823), `on_final_response_persisted` (11163).
- `STANDARD_HOOKS` in `hermes_cli/plugins.py` contains all 5 private-fork hook names + all 3 upstream kanban hook names after the conflict resolution.
- Targeted plugin test subsets: `tests/plugins/adaptive_routing`, `tests/plugins/account_usage`, `tests/plugins/gateway_noiseless_failover`, `tests/plugins/gateway_runtime_metadata`, `tests/plugins/message_allowlist` all green. `tests/test_plugin_hooks.py`, `tests/test_quota_service.py`, `tests/test_hermes_state.py`, `tests/gateway/test_runtime_footer.py`, `tests/gateway/test_unauthorized_dm_behavior.py`, `tests/gateway/test_usage_command.py`, `tests/agent/test_reasoning_policy.py`, `tests/hermes_cli/test_gateway.py` all green.
- One test-fix commit on top of the merge: `tests/gateway/test_compression_failure_session_sync.py` mock lambda updated to accept the new `reasoning_config` / `force_reasoning_config` kwargs the upstream call site now passes. This is the only private-fork test touched by the merge.
