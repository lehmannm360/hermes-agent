# Retire Adaptive-Routing Plugin — Simplify to a Fixed 3-Tier Model Stack

> **For Hermes:** Execute this plan task-by-task. Most tasks are config edits + validation; where verification commands are given, run them and confirm expected output before moving on.

**Goal:** Replace the bundled `adaptive-routing` plugin (difficulty-based, quota-aware model routing) with a fixed, ordered 3-tier model stack applied uniformly across **all** task difficulties and **all** Hermes profiles:

1. **Default:** DeepSeek V4 Flash via OpenCode Go
2. **Fallback 1:** GPT-5.6 Luna via OpenAI Codex
3. **Fallback 2:** DeepSeek V4 Flash via native DeepSeek API

**Architecture:** Use Hermes' native `model.default` + ordered `fallback_providers` chain (already supported by `gateway/run.py::_load_fallback_model` / `_apply_fallback_chain_to_agent` and `run_agent._try_activate_fallback`). Disable the adaptive-routing plugin and the legacy `agent.reasoning_policy` block so no difficulty-based routing remains. Fallback triggers on retryable primary failures (rate limit, 5xx, connection errors), not on difficulty.

**Tech Stack:** Hermes Agent (bundled plugin + native config), YAML config, launchd-supervised gateways, pytest (for a validation script), git (fork sync).

---

## Current State (verified 2026-08-02)

### Default profile — `~/.hermes/config.yaml` (555 lines)
- `model.default: deepseek-v4-flash`, `model.provider: opencode-go` ✅ already correct
- `fallback_providers` (lines 8–14): `deepseek-v4-pro` (opencode-go) → `gpt-5.5` (openai-codex) → `gpt-5.4-mini` (openai-codex) ❌ needs replacement
- `agent.reasoning_policy` (lines 41–64): `enabled: true`, deepseek via opencode-go, codex `gpt-5.6-luna` all difficulties ❌ to be removed
- `plugins.adaptive_routing` (lines 531–546) + `plugins.enabled` includes `adaptive-routing` (line 548) ❌ to be removed/disabled
- `auxiliary.vision`: openai-codex / gpt-5.6-luna (fine, not routing)
- `auxiliary.compression`: opencode-go / deepseek-v4-flash (fine)
- `agent.reasoning_effort: max` (line 40) — uniform; kept as-is (see Decisions)

### Content-agent profile — `~/.hermes/profiles/content-agent/config.yaml` (669 lines)
- `model.default: mimo-v2.5`, `model.provider: opencode-go`, `base_url: https://opencode.ai/zen/go/v1` (lines 1–4) ❌ MiMo default must go
- `fallback_providers` (lines 10–20): mimo-v2.5-pro (opencode-go) → gpt-5.5 → gpt-5.4-mini (openai-codex) → deepseek-v4-flash → deepseek-v4-pro (deepseek) ❌ needs replacement
- `agent.reasoning_policy` (lines 44–76): `enabled: true`, **MiMo keys present** (`mimo_provider`, `mimo_flash_model`, `mimo_pro_model`, `mimo_model_by_difficulty`) ❌ to be removed
- `plugins` (lines 646–666): `enabled` includes `adaptive-routing`; `adaptive_routing.stacks.primary` = routine/difficult mimo-v2.5 (opencode-go), complex gpt-5.5 (openai-codex) ❌ to be removed/disabled
- `auxiliary.vision` (lines 191–193): opencode-go / **mimo-v2.5** ❌ MiMo reference must be replaced

### Credentials (verified present in both `~/.hermes/.env` and `~/.hermes/profiles/content-agent/.env`)
- `OPENCODE_GO_API_KEY` ✅
- `DEEPSEEK_API_KEY` ✅
- OpenAI Codex OAuth configured ✅ (`hermes auth`)

### Active gateways
- `default` → launchd PID (currently ~87325), cmd `python -m hermes_cli.main gateway run --replace`
- `content-agent` → launchd PID (currently ~87327), cmd `python -m hermes_cli.main --profile content-agent gateway run --replace`

---

## Target State

### Default profile — `~/.hermes/config.yaml`
```yaml
model:
  default: deepseek-v4-flash
  provider: opencode-go
# ...hidden_providers, credential_pool_strategies, toolsets, agent.* unchanged (except reasoning_policy removed)...

fallback_providers:
  - provider: openai-codex
    model: gpt-5.6-luna
  - provider: deepseek
    model: deepseek-v4-flash

# agent.reasoning_policy block REMOVED entirely (absent = disabled per DEFAULT_REASONING_POLICY)
# plugins.adaptive_routing block REMOVED
plugins:
  enabled:
    - message-allowlist
    - gateway-noiseless-failover
    - gateway-runtime-metadata
```

### Content-agent profile — `~/.hermes/profiles/content-agent/config.yaml`
```yaml
model:
  default: deepseek-v4-flash
  provider: opencode-go
  # base_url removed unless required by provider resolution (verify with hermes model/chat -q)

fallback_providers:
  - provider: openai-codex
    model: gpt-5.6-luna
  - provider: deepseek
    model: deepseek-v4-flash

# agent.reasoning_policy REMOVED (kills mimo_* keys)
# plugins.adaptive_routing REMOVED; adaptive-routing removed from plugins.enabled
auxiliary:
  vision:
    provider: openai-codex
    model: gpt-5.6-luna   # replaced from mimo-v2.5
```

---

## Preflight

- [ ] Repo sync: `cd ~/.hermes/hermes-agent && git fetch agent main && git fetch origin main` (per standing git workflow). NOTE: `origin/main` has drifted significantly (4016 files, mostly `website/` restructure) — **do not merge upstream as part of this task**; that is a separate decision (see Out of Scope).
- [ ] Snapshot both configs: `cp ~/.hermes/config.yaml ~/.hermes/config.yaml.bak-20260802 && cp ~/.hermes/profiles/content-agent/config.yaml ~/.hermes/profiles/content-agent/config.yaml.bak-20260802`

---

## Task 1: Default profile — replace fallback_providers

**Objective:** Set the ordered 3-tier chain for the default profile.

**Files:** Modify `~/.hermes/config.yaml` lines 8–14.

**Step 1:** Replace the existing `fallback_providers` block (lines 8–14) with:

```yaml
fallback_providers:
  - provider: openai-codex
    model: gpt-5.6-luna
  - provider: deepseek
    model: deepseek-v4-flash
```

**Step 2:** Validate YAML parses:

Run: `python3 -c "import yaml; yaml.safe_load(open('/Users/lehmann/.hermes/config.yaml'))" && echo OK`
Expected: `OK`

**Step 3:** Verify effective chain:

Run: `hermes fallback list`
Expected:
```
Primary:   deepseek-v4-flash  (via opencode-go)
Fallback chain (2 entries):
  1. gpt-5.6-luna  (via openai-codex)
  2. deepseek-v4-flash  (via deepseek)
```

---

## Task 2: Default profile — disable adaptive routing

**Objective:** Remove the plugin config + legacy policy so no difficulty routing runs.

**Files:** Modify `~/.hermes/config.yaml` — delete `agent.reasoning_policy` block (lines 41–64), delete `plugins.adaptive_routing` block (lines 532–546), remove `adaptive-routing` from `plugins.enabled` (line 548).

**Step 1:** Delete the `reasoning_policy:` block under `agent:` (lines 41–64). The `agent:` section keeps `reasoning_effort: max` and all other keys.

**Step 2:** Delete the `adaptive_routing:` key under `plugins:` (lines 532–546).

**Step 3:** Remove `- adaptive-routing` from `plugins.enabled`, leaving:

```yaml
plugins:
  enabled:
    - message-allowlist
    - gateway-noiseless-failover
    - gateway-runtime-metadata
```

**Step 4:** Validate:

Run: `python3 -c "import yaml; yaml.safe_load(open('/Users/lehmann/.hermes/config.yaml'))" && echo OK`
Expected: `OK`

Run: `hermes plugins list | grep -i adaptive || echo 'adaptive-routing not enabled'`
Expected: `adaptive-routing not enabled` (plugin may still be listed as disabled/available — that's fine)

Run: `grep -nE 'reasoning_policy|adaptive_routing|adaptive-routing' ~/.hermes/config.yaml || echo 'no routing policy refs'`
Expected: `no routing policy refs`

---

## Task 3: Content-agent profile — fix model.default

**Objective:** Point content-agent's primary at DeepSeek V4 Flash via OpenCode Go, drop MiMo default and zen base_url.

**Files:** Modify `~/.hermes/profiles/content-agent/config.yaml` lines 1–4.

**Step 1:** Replace:

```yaml
model:
  default: mimo-v2.5
  provider: opencode-go
  base_url: https://opencode.ai/zen/go/v1
```

with:

```yaml
model:
  default: deepseek-v4-flash
  provider: opencode-go
```

**Step 2:** Validate:

Run: `python3 -c "import yaml; yaml.safe_load(open('/Users/lehmann/.hermes/profiles/content-agent/config.yaml'))" && echo OK`
Expected: `OK`

Run: `hermes --profile content-agent model` (non-interactive check is limited; use `hermes --profile content-agent chat -q 'ping' -Q --provider opencode-go` to confirm resolution) — see Task 7 for the full runtime check.

---

## Task 4: Content-agent profile — replace fallback_providers

**Objective:** Set the same ordered 3-tier chain.

**Files:** Modify `~/.hermes/profiles/content-agent/config.yaml` lines 10–20.

**Step 1:** Replace the 5-entry block (lines 10–20) with:

```yaml
fallback_providers:
  - provider: openai-codex
    model: gpt-5.6-luna
  - provider: deepseek
    model: deepseek-v4-flash
```

**Step 2:** Validate:

Run: `python3 -c "import yaml; yaml.safe_load(open('/Users/lehmann/.hermes/profiles/content-agent/config.yaml'))" && echo OK`
Expected: `OK`

Run: `hermes --profile content-agent fallback list`
Expected:
```
Primary:   deepseek-v4-flash  (via opencode-go)
Fallback chain (2 entries):
  1. gpt-5.6-luna  (via openai-codex)
  2. deepseek-v4-flash  (via deepseek)
```

---

## Task 5: Content-agent profile — remove MiMo reasoning policy + disable plugin

**Objective:** Remove all MiMo references and adaptive routing from content-agent.

**Files:** Modify `~/.hermes/profiles/content-agent/config.yaml` — delete `agent.reasoning_policy` (lines 44–76, contains `mimo_provider` etc.), delete `plugins.adaptive_routing` (lines 652–666), remove `adaptive-routing` from `plugins.enabled` (line 648).

**Step 1:** Delete the `reasoning_policy:` block (lines 44–76). This removes `mimo_provider`, `mimo_flash_model`, `mimo_pro_model`, `mimo_model_by_difficulty` and all codex/deepseek policy keys.

**Step 2:** Delete the `adaptive_routing:` key under `plugins:` (lines 652–666).

**Step 3:** Remove `- adaptive-routing` from `plugins.enabled` (line 648).

**Step 4:** Validate — no MiMo anywhere in active config:

Run: `python3 -c "import yaml; yaml.safe_load(open('/Users/lehmann/.hermes/profiles/content-agent/config.yaml'))" && echo OK`
Expected: `OK`

Run: `grep -niE 'mimo|reasoning_policy|adaptive' ~/.hermes/profiles/content-agent/config.yaml || echo 'clean'`
Expected: `clean`

Run: `grep -niE 'mimo' ~/.hermes/config.yaml || echo 'clean'`
Expected: `clean`

---

## Task 6: Content-agent profile — fix auxiliary vision model

**Objective:** Remove the last MiMo reference (vision) so no auxiliary task can resolve to MiMo.

**Files:** Modify `~/.hermes/profiles/content-agent/config.yaml` lines 191–193.

**Step 1:** Replace:

```yaml
  vision:
    provider: opencode-go
    model: mimo-v2.5
```

with:

```yaml
  vision:
    provider: openai-codex
    model: gpt-5.6-luna
```

**Step 2:** Validate:

Run: `grep -niE 'mimo' ~/.hermes/profiles/content-agent/config.yaml || echo 'clean'`
Expected: `clean`

---

## Task 7: Pre-restart validation (both profiles)

**Objective:** Confirm no stale routing code paths will fire and credentials resolve.

**Step 1:** Confirm the adaptive-routing plugin's hook won't run (policy absent = disabled):

Run:
```bash
cd ~/.hermes/hermes-agent
PYTHONPATH=. venv/bin/python - <<'PY'
from hermes_cli.config import load_config
from gateway.run import GatewayRunner
for label, path in [('default','/Users/lehmann/.hermes/config.yaml'),('content-agent','/Users/lehmann/.hermes/profiles/content-agent/config.yaml')]:
    import yaml
    cfg = yaml.safe_load(open(path)) or {}
    pol = (cfg.get('agent') or {}).get('reasoning_policy') or {}
    print(label, 'policy enabled =', pol.get('enabled', False))
PY
```
Expected: both print `policy enabled = False`

**Step 2:** Verify both profiles can resolve the primary provider (short smoke query; no model change):

Run: `hermes chat -q 'reply with the word OK' -Q 2>&1 | tail -3`
Expected: contains `OK` (uses opencode-go / deepseek-v4-flash)

Run: `hermes --profile content-agent chat -q 'reply with the word OK' -Q 2>&1 | tail -3`
Expected: contains `OK`

**Step 3:** Confirm no `mimo` remains in either `.env`-adjacent active config or plugin stack:

Run: `grep -rniE 'mimo' ~/.hermes/config.yaml ~/.hermes/profiles/content-agent/config.yaml ~/.hermes/plugins 2>/dev/null | grep -v Binary || echo 'clean'`
Expected: `clean` (bundled plugin source under the repo's `plugins/` tree may still mention MiMo in code — that's inactive source, see Out of Scope)

---

## Task 8: Restart both gateways

**Objective:** Load the new config into the running services. A restart is required because fallback chain + policy are read at process/agent-create time.

**Step 1:** Restart default gateway:

Run: `launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway`
Expected: exit 0 (note: run from a shell outside the gateway if the guard blocks self-restart)

**Step 2:** Restart content-agent gateway:

Run: `launchctl kickstart -k gui/$(id -u)/ai.hermes.gateway-content-agent`
Expected: exit 0 (confirm actual label with `hermes gateway status`; it may be `ai.hermes.gateway-content-agent` or similar)

**Step 3:** Wait for readiness, then verify:

Run: `hermes gateway status`
Expected: both profiles `running` with new PIDs

Run: `launchctl print gui/$(id -u)/ai.hermes.gateway 2>/dev/null | grep -E 'pid =|state ='`
Expected: `state = running`, fresh pid

---

## Task 9: Post-restart verification

**Objective:** Prove the live system is on the fixed 3-tier stack.

**Step 1:** Primary model per profile:

Run: `hermes profile list`
Expected:
```
◆default         deepseek-v4-flash   running
 content-agent   deepseek-v4-flash   running
```

**Step 2:** Fallback chain per profile:

Run: `hermes fallback list` and `hermes --profile content-agent fallback list`
Expected (both):
```
Primary:   deepseek-v4-flash  (via opencode-go)
Fallback chain (2 entries):
  1. gpt-5.6-luna  (via openai-codex)
  2. deepseek-v4-flash  (via deepseek)
```

**Step 3:** Confirm no adaptive route label in runtime footer / session info. Start a session and check `/status` (or the gateway footer) shows `deepseek-flash` / opencode-go and NO `route_label` of `luna`/`codex` on routine tasks. Confirm `route_source` is not `adaptive`.

**Step 4:** Verify fallback actually engages (optional but recommended): temporarily break the primary by setting `model.base_url` to an unreachable value in a scratch copy, run one query, confirm it lands on gpt-5.6-luna, then restore. Alternatively check logs after a forced 429 simulation:
Run: `grep -iE 'fallback|switching' ~/.hermes/logs/gateway.log | tail -20`
Expected: no fallback entries during normal operation (or entries only when a fallback was genuinely triggered).

**Step 5:** Sanity-check auxiliary tasks still work: `hermes chat -q 'describe this image: /tmp/test.png' -Q` (vision) and a compression-heavy long conversation. Confirm no `mimo` errors in logs:
Run: `grep -iE 'mimo' ~/.hermes/logs/gateway.log | tail -5 || echo 'no mimo refs'`

---

## Task 10: Commit the plan + any repo-side artifacts

**Objective:** Persist the plan in the hermes-agent fork (`docs/plans/`), per the standing fetch→merge→push workflow.

**Files:**
- Create: `docs/plans/2026-08-02-retire-adaptive-routing-simplify-model-stack.md` (this file)

**Step 1:** Stage + commit:

```bash
cd ~/.hermes/hermes-agent
git add docs/plans/2026-08-02-retire-adaptive-routing-simplify-model-stack.md
git commit -m "docs(plans): retire adaptive-routing plugin for fixed 3-tier model stack"
```

**Step 2:** Push to fork and verify:

```bash
git fetch agent main
git push agent main
git status --short --branch
git rev-parse HEAD && git rev-parse agent/main
git log -1 --oneline --decorate
```
Expected: `## main...agent/main` in sync; both `rev-parse` SHAs match; HEAD shows the plan commit.

**Step 3 (no-op if configs aren't repo-tracked):** Config YAMLs live under `~/.hermes/`, not the repo — do not commit them here. If any future profile scaffold is repo-tracked, keep those edits separate.

---

## Rollback Plan

If anything regresses after restart:

1. Restore snapshots: `cp ~/.hermes/config.yaml.bak-20260802 ~/.hermes/config.yaml` (and same for content-agent).
2. Re-add `- adaptive-routing` to `plugins.enabled` in both profiles (the bundled plugin source remains installed — no code restore needed).
3. Restart both gateways (Task 8 commands).
4. Verify `hermes profile list` shows prior models and `hermes plugins list` shows adaptive-routing enabled.

**Why rollback is cheap:** the bundled plugin is left installed (just disabled) and config snapshots are taken before any edit. The plugin code is not deleted in this plan.

---

## Verification Checklist (final sign-off)

- [x] `hermes fallback list` (default) = `gpt-5.6-luna (openai-codex)` → `deepseek-v4-flash (deepseek)` — verified live
- [x] `hermes --profile content-agent fallback list` = same 2 entries — verified live
- [x] `hermes profile list` shows `deepseek-v4-flash` for both profiles — both running
- [x] No `mimo`, `reasoning_policy`, or `adaptive_routing` refs in either active config — zero hits both files
- [x] Both gateways running under launchd with fresh PIDs — `ai.hermes.gateway` + `ai.hermes.gateway-content-agent`
- [x] Smoke chat on both profiles returns normally via opencode-go — default 8s, content-agent 7s
- [x] No adaptive `route_label`/`route_source` in runtime footer — `route_label=None` post-restart (FOOTER_DEBUG)
- [x] Plan committed to fork, pushed, SHAs match — `4eb0ea9c13`, HEAD == agent/main
- [x] Vision auxiliary sanity — both profiles answer via gpt-5.6-luna (see Execution Log §4 for the auth fix)
- [x] Fallback/switch log check — no model-fallback entries during normal operation (only Telegram network fallback)
- [ ] Fallback engagement forced test — SKIPPED (would require inducing a primary failure; log-based check performed instead)

---

## Execution Log — 2026-08-02 (all 10 tasks executed same day)

### §1 Tasks 1–7: config edits (interactive)
All edits applied with line-splicing (rest of file preserved byte-for-byte), YAML validated after each, snapshots taken first: `~/.hermes/config.yaml.bak-20260802`, `~/.hermes/profiles/content-agent/config.yaml.bak-20260802`.

- Default profile: `fallback_providers` → 3-tier chain (Task 1); `agent.reasoning_policy` + `plugins.adaptive_routing` + plugin enable removed (Task 2).
- Content-agent: `model.default` → deepseek-v4-flash/opencode-go, zen `base_url` dropped (Task 3); fallback chain replaced (Task 4); `mimo_*` policy + plugin removed (Task 5); `auxiliary.vision` → openai-codex/gpt-5.6-luna (Task 6).
- Task 7 static validation passed: zero `mimo`/`adaptive` hits in both configs, both YAML valid, fallback lists correct on both profiles, plugin shows `not enabled`, smoke chats OK (8s / 7s).

### §2 Task 8 restart incident (avoidable — lesson learned)
The restart card was kanban-assigned to workers running ON the gateways they had to kickstart → each worker killed its own gateway mid-task → crash → dispatcher retried (2 failures) → **manual intervention by Esa** to stabilize. The gateways did restart successfully with the new config. Lesson patched into `hermes-adaptive-routing` skill: restarts are operator-executed, never kanban-assigned. Card K8 completed with honest summary after stabilization.

### §3 Follow-up found by worker: residual MiMo refs outside plan scope
During post-restart flow, a worker found and purged (card `t_f8816825`, done):
- `~/.hermes/profiles/content-agent/.env`: `MNEMOSYNE_HOST_LLM_MODEL=mimo-v2.5` → `deepseek-v4-flash`; `XIAOMI_API_KEY` disabled; `XIAOMI_BASE_URL` commented (checked: `model-providers/xiaomi` plugin not enabled, so not load-bearing; `hidden_providers` entry untouched).
- `~/.hermes/cron/jobs.json`: both sleep-consolidation prompts (daily-obsidian-memory-maintenance 03:00 MYT, content-agent 02:20 MYT) rewritten to "deepseek-v4-flash (via opencode-go)". JSON valid, 18 jobs.
- 2 stale Mnemosyne memories referencing mimo-v2.5 invalidated.

### §4 Sign-off caught a real auth gap (Task 6 side effect)
`auxiliary.vision` → gpt-5.6-luna assumed Codex creds on content-agent. Reality: content-agent's `providers.openai-codex` tokens were stale (2026-07-05) and its pool `device_code` entry was `exhausted` → auxiliary vision (and fallback #1) could not resolve openai-codex. Default profile worked because it has no `providers.openai-codex` key and a fresh pool entry (`codex-phone-auth`, refreshed 2026-08-02 04:06).
**Fix:** mirrored default's proven structure into `~/.hermes/profiles/content-agent/auth.json` — removed stale `providers.openai-codex`, replaced pool entry with fresh `codex-phone-auth` copy. Backup: `auth.json.bak-20260802`. Vision re-verified on both profiles (solid-red test PNG answered correctly). No codex resolution errors after fix.

### §5 Sign-off results
Footer `route_label=None` post-restart (old `mimo` labels are pre-restart history); no model-fallback entries in gateway log (only Telegram network fallback); zero current MiMo refs in both gateway logs; forced-fallback test intentionally skipped (would require inducing a primary failure).

### §6 Repo state
Plan commit `4eb0ea9c13` was pushed before execution. Configs live outside the repo. `package-lock.json` shows a pre-existing unrelated modification (not committed, per standing workflow).

---

## Decisions & Notes

1. **Uniform difficulty:** Per user requirement, all three tiers apply to ALL task difficulties. No difficulty classification remains after this migration.
2. **`agent.reasoning_effort: max`** (default profile) is kept as-is — it is the uniform reasoning level, independent of routing. Optionally reduce to `high` later if DeepSeek Flash latency/cost matters; not part of this plan.
3. **Bundled plugin stays installed but disabled** for a cheap rollback path. Deleting the plugin source is a follow-up decision after N days of stable operation.
4. **`hidden_providers: [xiaomi, ...]`** stays — prevents MiMo from reappearing in the interactive picker.
5. **Fallback semantics:** chain triggers on retryable failures (rate-limit, overload/5xx, connection). It does NOT pre-empt based on task difficulty. This matches the requested fixed-stack behavior.

## Out of Scope (separate follow-ups)

- **Upstream merge:** `origin/main` has drifted ~4016 files (mostly `website/` skill-docs restructure) since our fork's last sync. Merging that into the live runtime checkout is a separate, riskier operation (full test suite + fork-delta audit per `private-fork-update-audit` skill) — do NOT bundle it with this config migration. **Status: still pending** — recommend a dedicated slot with its own test run.
- **Plugin code deletion:** removing `plugins/adaptive-routing/` from the repo + tests is a later cleanup after the stack has proven stable. **Status: pending** — stack is stable as of 2026-08-02; deletion recommended after 2–3 days of clean fallback/operation.
- **Content-agent gateway launchd label** — **confirmed** during execution: `ai.hermes.gateway-content-agent` (plus `ai.hermes.gateway` for default); the kanban dispatcher is embedded in the gateway (60s tick, singleton lock).
- **Codex auth for content-agent** — resolved during sign-off (see Execution Log §4); a proper `hermes -p content-agent auth` re-auth is optional future hygiene, since the fix mirrored default's pool entry.
