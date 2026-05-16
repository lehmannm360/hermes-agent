# WhatsApp Communication Implementation Plan

> **For Hermes:** Use `subagent-driven-development` skill to implement this plan task-by-task. Load `hermes-agent`, `writing-plans`, `test-driven-development`, and `requesting-code-review` before coding.

**Goal:** Build a safe WhatsApp communication channel that lets Hermes send approved operational messages to Esa's whitelisted colleagues, receive their replies, summarize them back to Telegram, and eventually automate daily business performance reports.

**Architecture:** Run Hermes and a small WhatsApp Cloud API bridge on Esa's Mac Mini. Expose the bridge webhook to Meta via Cloudflare Tunnel. Use Meta's official WhatsApp Business Cloud API, not WhatsApp Web/Baileys automation, for production communication. Store durable state in Supabase Postgres. Keep Telegram as the control/approval channel.

**Tech Stack:** Python 3, FastAPI, Supabase Postgres, Meta WhatsApp Business Cloud API, Cloudflare Tunnel, Hermes gateway/cron, macOS launchd.

---

## Context and Decisions Already Made

- Esa has a rarely used WhatsApp Business number that is suitable for an assistant identity.
- Preferred integration path: **official Meta WhatsApp Business Cloud API**.
- Avoid relying on WhatsApp Web / Baileys / browser automation for production colleague messaging.
- Third-party providers like Twilio are **not necessary** for the preferred setup.
- Hosting target: the same **Mac Mini** that hosts Hermes.
- Public webhook exposure: prefer **Cloudflare Tunnel** over router port forwarding.
- Database: use **Supabase** instead of local SQLite.
- User-facing control channel: **Telegram** remains the approval/control interface.
- Implementation should live in Esa's/private Hermes fork or local custom extension first; upstream only later if generic enough.
- Start safe: approval-gated outbound messages, whitelisted recipients only, minimal templates, logs/audit trail.

## Non-Goals for MVP

Do not implement these in the first pass unless explicitly requested:

- Twilio/third-party WhatsApp provider integration.
- WhatsApp Web / Baileys production automation.
- Full admin dashboard.
- Multi-user approval workflows.
- Complex CRM/ERP integration.
- Automated sensitive replies without Esa approval.
- Group broadcasting beyond a small controlled pilot.
- End-to-end encryption handling beyond WhatsApp/Meta's platform guarantees.

## Safety Principles

- No proactive WhatsApp message should be sent to a non-whitelisted recipient.
- Start in approval-required mode for all outbound proactive messages.
- Only use approved Meta templates for messages outside the customer service window.
- Free-form replies are only allowed inside the WhatsApp customer service window and only when policy allows.
- Sensitive topics require Esa approval: HR, legal, pricing, client disputes, financial commitments, personal information, disciplinary matters, confidential strategy.
- Every outbound and inbound WhatsApp event must be logged.
- Every AI-generated message should be traceable to source data or user approval.
- Use hard send limits during pilot, e.g. max 50 sends/day and max 1 follow-up per request.

---

## Proposed Repository Layout

Prefer a small custom service under the Hermes repo until we decide whether to split it out:

```text
integrations/whatsapp_cloud/
  __init__.py
  app.py                    # FastAPI app and routes
  config.py                 # env/config loading
  meta_client.py            # WhatsApp Cloud API client
  supabase_store.py         # Supabase persistence wrapper
  policy.py                 # allowlist, approval, send-window rules
  schemas.py                # pydantic models
  templates.py              # template definitions / validation helpers
  telegram_approval.py      # helper to send approval prompts via Hermes/send_message path if needed
  service.py                # orchestration layer
  README.md
  launchd/com.hermes.whatsapp-cloud.plist.template

tests/integrations/whatsapp_cloud/
  test_policy.py
  test_meta_client.py
  test_webhook.py
  test_supabase_store.py
  test_service.py

docs/plans/2026-05-16-whatsapp-communication-implementation.md
```

If keeping all custom code outside the Hermes repo is preferred later, mirror this layout in a private repo and treat Hermes as the caller/control plane.

---

## Required Secrets and Config

Store secrets locally on the Mac Mini in `~/.hermes/.env` or a dedicated service `.env` file with restrictive permissions.

```env
WHATSAPP_ACCESS_TOKEN=...
WHATSAPP_PHONE_NUMBER_ID=...
WHATSAPP_BUSINESS_ACCOUNT_ID=...
WHATSAPP_VERIFY_TOKEN=...
WHATSAPP_APP_SECRET=...
SUPABASE_URL=...
SUPABASE_SERVICE_ROLE_KEY=...
WHATSAPP_BRIDGE_BASE_URL=http://127.0.0.1:8787
WHATSAPP_APPROVAL_MODE=required
WHATSAPP_DAILY_SEND_LIMIT=50
```

Notes:

- The Supabase service role key must never be exposed to a browser/client.
- The webhook verify token should be a random secret, not a human-readable word.
- Consider validating Meta webhook signatures using `WHATSAPP_APP_SECRET` before accepting POST events.

---

## Supabase MVP Schema

Create these tables first. Keep them simple and expand only when needed.

```sql
create table whatsapp_contacts (
  id uuid primary key default gen_random_uuid(),
  display_name text not null,
  role text,
  team text,
  whatsapp_phone text not null unique,
  whatsapp_id text unique,
  timezone text default 'Asia/Kuala_Lumpur',
  allowlisted boolean not null default false,
  active boolean not null default true,
  notes text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table whatsapp_approval_requests (
  id uuid primary key default gen_random_uuid(),
  contact_id uuid references whatsapp_contacts(id),
  recipient_phone text not null,
  request_type text not null, -- template | freeform | followup | report
  draft_text text not null,
  template_name text,
  template_variables jsonb default '{}'::jsonb,
  status text not null default 'pending', -- pending | approved | rejected | expired | sent
  requested_by text not null default 'assistant',
  approved_by text,
  approval_channel text default 'telegram',
  telegram_message_ref text,
  created_at timestamptz not null default now(),
  decided_at timestamptz,
  sent_message_id uuid
);

create table whatsapp_message_logs (
  id uuid primary key default gen_random_uuid(),
  contact_id uuid references whatsapp_contacts(id),
  direction text not null, -- outbound | inbound | status
  whatsapp_message_id text,
  recipient_phone text,
  sender_phone text,
  message_kind text not null, -- template | text | image | status | system
  template_name text,
  template_category text, -- utility | marketing | authentication | service | unknown
  body text,
  variables jsonb default '{}'::jsonb,
  meta_status text,
  approval_request_id uuid references whatsapp_approval_requests(id),
  cost_estimate_myr numeric(10,4),
  raw_payload jsonb,
  created_at timestamptz not null default now()
);

create table whatsapp_inbound_messages (
  id uuid primary key default gen_random_uuid(),
  contact_id uuid references whatsapp_contacts(id),
  whatsapp_message_id text unique,
  sender_phone text not null,
  body text,
  message_type text,
  raw_payload jsonb not null,
  summarized boolean not null default false,
  created_at timestamptz not null default now()
);

create table whatsapp_conversation_windows (
  id uuid primary key default gen_random_uuid(),
  contact_id uuid references whatsapp_contacts(id),
  whatsapp_phone text not null unique,
  last_inbound_at timestamptz,
  freeform_until timestamptz,
  updated_at timestamptz not null default now()
);

create table whatsapp_audit_log (
  id uuid primary key default gen_random_uuid(),
  actor text not null, -- esa | assistant | meta_webhook | system
  event_type text not null,
  entity_type text,
  entity_id text,
  details jsonb default '{}'::jsonb,
  created_at timestamptz not null default now()
);
```

Add indexes after the MVP works:

```sql
create index on whatsapp_message_logs (created_at desc);
create index on whatsapp_message_logs (whatsapp_message_id);
create index on whatsapp_inbound_messages (created_at desc);
create index on whatsapp_approval_requests (status, created_at desc);
```

---

## Message Templates for First Submission

Submit 3-5 narrow WhatsApp utility templates to Meta first.

### Template 1: `status_follow_up`

```text
Hi {{1}}, quick follow-up on {{2}}.

Current status needed: {{3}}
Deadline: {{4}}

Please reply here with the latest update.
```

### Template 2: `daily_performance_report`

```text
Hi {{1}}, here is the daily business performance report for {{2}}.

Revenue: {{3}}
Orders: {{4}}
Fulfillment: {{5}}
Key issue: {{6}}
Next action: {{7}}

Reply here if you have questions or updates.
```

### Template 3: `action_required`

```text
Hi {{1}}, action is needed for {{2}}.

Task: {{3}}
Due by: {{4}}
Context: {{5}}

Please confirm once done.
```

### Template 4: `exception_alert`

```text
Hi {{1}}, there is an exception for {{2}}.

Issue: {{3}}
Impact: {{4}}
Recommended next step: {{5}}

Please review and reply with your input.
```

### Template 5: `input_request`

```text
Hi {{1}}, Esa's assistant is collecting input for {{2}}.

Question: {{3}}
Needed by: {{4}}

Please reply here with your feedback.
```

---

## Implementation Tasks

### Task 1: Create the integration skeleton

**Objective:** Add the WhatsApp Cloud integration package and placeholder tests.

**Files:**
- Create: `integrations/whatsapp_cloud/__init__.py`
- Create: `integrations/whatsapp_cloud/config.py`
- Create: `integrations/whatsapp_cloud/schemas.py`
- Create: `tests/integrations/whatsapp_cloud/test_policy.py`

**Steps:**
1. Create the directories and empty package file.
2. Add a minimal config dataclass that reads required env vars without printing secrets.
3. Add pydantic/dataclass schemas for `Contact`, `OutboundTemplateRequest`, `InboundWebhookEvent`, and `ApprovalRequest`.
4. Add a placeholder policy test that imports the package.
5. Run: `python -m pytest tests/integrations/whatsapp_cloud/test_policy.py -q -o 'addopts='`.
6. Commit: `feat: add whatsapp cloud integration skeleton`.

**Verification:** Import succeeds and the test file runs.

---

### Task 2: Implement policy checks

**Objective:** Centralize safety rules before any outbound send.

**Files:**
- Create: `integrations/whatsapp_cloud/policy.py`
- Modify: `tests/integrations/whatsapp_cloud/test_policy.py`

**Policy behavior:**
- Reject if contact is not allowlisted.
- Reject if contact is inactive.
- Require approval for proactive outbound messages while `WHATSAPP_APPROVAL_MODE=required`.
- Allow free-form reply only when `freeform_until > now`.
- Reject sensitive categories unless explicitly approved.
- Enforce daily send limit.

**Test cases:**
- non-allowlisted contact cannot be messaged
- inactive contact cannot be messaged
- proactive template requires approval in required mode
- free-form reply outside window is blocked
- free-form reply inside window is allowed
- sensitive message requires approval
- daily send limit blocks additional messages

**Run:**
```bash
python -m pytest tests/integrations/whatsapp_cloud/test_policy.py -q -o 'addopts='
```

**Commit:** `feat: add whatsapp safety policy checks`.

---

### Task 3: Implement Meta WhatsApp Cloud API client

**Objective:** Add a small client for sending template and text messages through Meta Graph API.

**Files:**
- Create: `integrations/whatsapp_cloud/meta_client.py`
- Create: `tests/integrations/whatsapp_cloud/test_meta_client.py`

**Client methods:**
- `send_template(to_phone, template_name, language_code, variables)`
- `send_text(to_phone, body)`
- `parse_send_response(response_json)`

**Requirements:**
- Do not log access tokens.
- Use timeouts.
- Surface Meta API errors clearly.
- Return Meta message IDs.
- Keep network calls mockable for tests.

**Tests:**
- template request builds expected payload
- text request builds expected payload
- successful response extracts message ID
- error response raises clear exception
- token is not included in exception string/loggable output

**Run:**
```bash
python -m pytest tests/integrations/whatsapp_cloud/test_meta_client.py -q -o 'addopts='
```

**Commit:** `feat: add whatsapp cloud api client`.

---

### Task 4: Implement Supabase persistence wrapper

**Objective:** Store contacts, approvals, messages, inbound events, and conversation windows in Supabase.

**Files:**
- Create: `integrations/whatsapp_cloud/supabase_store.py`
- Create: `tests/integrations/whatsapp_cloud/test_supabase_store.py`
- Create: `integrations/whatsapp_cloud/sql/001_initial_schema.sql`

**Methods:**
- `get_contact_by_phone(phone)`
- `create_approval_request(...)`
- `mark_approval_decision(...)`
- `log_outbound_message(...)`
- `log_inbound_message(...)`
- `update_conversation_window(phone, last_inbound_at)`
- `count_outbound_messages_since(start_time)`
- `write_audit_event(...)`

**Tests:**
- Use a fake Supabase client / stub, not live Supabase credentials.
- Verify methods call the expected table operations.
- Verify service role key is not printed.

**Run:**
```bash
python -m pytest tests/integrations/whatsapp_cloud/test_supabase_store.py -q -o 'addopts='
```

**Commit:** `feat: add supabase storage for whatsapp bridge`.

---

### Task 5: Implement webhook verification and inbound parsing

**Objective:** Receive Meta webhook verification requests and inbound WhatsApp messages.

**Files:**
- Create: `integrations/whatsapp_cloud/app.py`
- Create: `tests/integrations/whatsapp_cloud/test_webhook.py`

**Endpoints:**
- `GET /whatsapp/webhook` for Meta challenge verification.
- `POST /whatsapp/webhook` for incoming events.
- `GET /healthz` for local/tunnel health checks.

**Behavior:**
- Verify `hub.verify_token` matches `WHATSAPP_VERIFY_TOKEN`.
- Return `hub.challenge` on valid verification.
- Reject invalid verification.
- Parse inbound messages from Meta payload.
- Store inbound messages in Supabase.
- Update the 24-hour conversation window.
- Ignore duplicate inbound message IDs.
- Log raw payload for audit/debugging.

**Tests:**
- valid verification returns challenge
- invalid verification returns 403
- inbound text payload is parsed and stored
- duplicate inbound message is idempotent
- unsupported payloads are safely ignored/logged

**Run:**
```bash
python -m pytest tests/integrations/whatsapp_cloud/test_webhook.py -q -o 'addopts='
```

**Commit:** `feat: add whatsapp webhook receiver`.

---

### Task 6: Implement outbound orchestration service

**Objective:** Combine policy, approval, storage, and Meta sending into one safe service.

**Files:**
- Create: `integrations/whatsapp_cloud/service.py`
- Create: `tests/integrations/whatsapp_cloud/test_service.py`

**Service methods:**
- `request_template_send(contact_phone, template_name, variables, reason)`
- `approve_and_send(approval_request_id, approved_by='esa')`
- `reject_approval(approval_request_id, rejected_by='esa')`
- `send_freeform_reply(contact_phone, body, reason)`

**Behavior:**
- If approval is required, create approval request and do not send.
- If approval is not required and policy allows, send immediately.
- Log outbound messages after successful Meta send.
- Write audit events for approval requested, approved, rejected, sent, blocked.
- Never bypass allowlist.

**Tests:**
- request creates pending approval instead of sending
- approval sends template and logs message
- rejection does not send
- non-allowlisted contact is blocked
- free-form reply outside window is blocked
- free-form reply inside window sends as text

**Run:**
```bash
python -m pytest tests/integrations/whatsapp_cloud/test_service.py -q -o 'addopts='
```

**Commit:** `feat: add whatsapp outbound orchestration`.

---

### Task 7: Add Telegram approval workflow

**Objective:** Let Hermes ask Esa on Telegram before sending proactive WhatsApp messages.

**Files:**
- Create: `integrations/whatsapp_cloud/telegram_approval.py`
- Modify: `integrations/whatsapp_cloud/service.py`
- Add tests in: `tests/integrations/whatsapp_cloud/test_service.py`

**MVP approach:**
- When a WhatsApp send requires approval, create a Supabase `whatsapp_approval_requests` row.
- Return a structured approval prompt text for Telegram.
- Esa can approve by asking Hermes to approve a specific request ID, or by a later command/hook if implemented.

**Prompt format:**

```text
WhatsApp approval needed
Recipient: Sarah (+60...)
Template: status_follow_up
Reason: daily ops follow-up
Draft:
...
Approval ID: <uuid>

Reply: approve <uuid> or reject <uuid>
```

**Tests:**
- approval prompt includes recipient, template, draft, and approval ID
- prompt does not expose secrets

**Commit:** `feat: add telegram approval prompts for whatsapp sends`.

---

### Task 8: Add CLI/test utility for manual sends

**Objective:** Provide a safe local command for smoke-testing the integration without relying on the full agent loop.

**Files:**
- Create: `integrations/whatsapp_cloud/cli.py`
- Modify: `integrations/whatsapp_cloud/README.md`

**Commands:**

```bash
python -m integrations.whatsapp_cloud.cli health
python -m integrations.whatsapp_cloud.cli request-template --to +60... --template status_follow_up --var Esa --var dispatch --var latest-status --var 4pm
python -m integrations.whatsapp_cloud.cli approve <approval-id>
```

**Requirements:**
- Dry-run mode by default if `--send` is not passed.
- Never print access tokens.
- Refuse non-allowlisted recipients.

**Commit:** `feat: add whatsapp cloud cli utilities`.

---

### Task 9: Add Cloudflare Tunnel and macOS launchd docs

**Objective:** Document how to run the bridge reliably on the Mac Mini.

**Files:**
- Create: `integrations/whatsapp_cloud/README.md`
- Create: `integrations/whatsapp_cloud/launchd/com.hermes.whatsapp-cloud.plist.template`

**README sections:**
- Meta setup checklist
- Supabase setup checklist
- environment variables
- running locally with uvicorn
- configuring Cloudflare Tunnel
- Meta webhook URL setup
- installing launchd service
- log locations
- health checks
- rollback/disable procedure

**launchd template:**
- Runs the FastAPI service on `127.0.0.1:8787`.
- Restarts on failure.
- Writes logs to `~/.hermes/logs/whatsapp-cloud.log` and `~/.hermes/logs/whatsapp-cloud.err.log`.

**Commit:** `docs: add whatsapp cloud mac mini deployment guide`.

---

### Task 10: End-to-end pilot with one test contact

**Objective:** Verify real Meta/Supabase/webhook behavior with a single approved test recipient.

**Prerequisites:**
- Meta app configured.
- WhatsApp Business number connected to Cloud API.
- At least one template approved by Meta.
- Cloudflare Tunnel public webhook URL configured.
- Supabase schema migrated.
- One test contact allowlisted.

**Steps:**
1. Start local service.
2. Confirm `GET /healthz` works locally and through Cloudflare Tunnel.
3. Complete Meta webhook verification.
4. Request a template send in dry-run mode.
5. Request a real approved send to test recipient.
6. Confirm Meta returns message ID.
7. Confirm row appears in `whatsapp_message_logs`.
8. Ask recipient to reply.
9. Confirm inbound webhook is received.
10. Confirm row appears in `whatsapp_inbound_messages`.
11. Confirm `whatsapp_conversation_windows.freeform_until` is updated.
12. Send a free-form confirmation reply inside the open window.
13. Review audit log.

**Success criteria:**
- Outbound template delivered.
- Inbound reply captured.
- Free-form reply sent inside customer service window.
- All events logged in Supabase.
- No non-allowlisted sends possible.

**Commit:** `test: document whatsapp pilot results` if any docs/config examples are updated.

---

## Cost/Billing Reference

As discussed, for Malaysia recipients on Meta's current MYR rate card effective April 1, 2026:

- Utility template: MYR 0.0564 per delivered message.
- Marketing template: MYR 0.3467 per delivered message.
- Free-form non-template replies inside an open customer service window are generally free.

Budget guideline for 1,000 Malaysia utility template messages/month: **~MYR 56.40**, with a practical pilot budget of **MYR 100/month** to allow for testing and variance. If messages are classified as marketing, budget closer to **MYR 350-400/month**.

Always re-check Meta pricing before production rollout.

---

## Deployment Checklist

- [ ] Mac Mini sleep disabled / stays awake when display is off.
- [ ] Hermes gateway runs reliably.
- [ ] Supabase project created.
- [ ] Supabase schema applied.
- [ ] `.env` populated with WhatsApp and Supabase secrets.
- [ ] WhatsApp Cloud API app configured in Meta.
- [ ] Business phone number connected.
- [ ] Webhook verify token generated.
- [ ] Cloudflare Tunnel configured with stable HTTPS URL.
- [ ] Meta webhook URL verified.
- [ ] Templates submitted and approved.
- [ ] First test contact allowlisted.
- [ ] Daily send limit set low for pilot.
- [ ] Approval mode set to `required`.
- [ ] launchd service installed and verified.
- [ ] Logs visible in `~/.hermes/logs/`.
- [ ] Disable/rollback procedure tested.

---

## Rollout Plan

### Phase 0: Manual drafting

No code. Hermes drafts WhatsApp messages in Telegram; Esa manually sends them. Use this to refine tone/templates.

### Phase 1: Outbound approved template MVP

Build tasks 1-7. Send only approval-gated template messages to 1-3 allowlisted colleagues.

### Phase 2: Inbound replies and summaries

Build webhook flow and summary prompts. Hermes reports replies back to Telegram. Free-form replies require policy check and, initially, approval for anything ambiguous.

### Phase 3: Daily business performance reports

Connect the actual data source, generate report variables, request approval in Telegram, send via `daily_performance_report` template, collect replies.

### Phase 4: Controlled automation

Allow specific low-risk sends without per-message approval, e.g. daily report to a fixed allowlisted group/contact list. Keep audit logs and send limits.

---

## Open Questions for Project Start

Ask Esa these before implementation begins:

1. What domain/subdomain should Cloudflare Tunnel use for the webhook?
2. Does Esa already have a Meta Business account connected to the spare WhatsApp Business number?
3. Which country codes will most colleagues use? Malaysia only, or mixed?
4. Which 3-5 colleagues should be in the first pilot allowlist?
5. Should messages identify themselves as `Esa's assistant` in every template?
6. Which business data source should daily reports use first?
7. Should initial approval be per-message, or can daily reports to a fixed contact list be pre-approved after pilot?
8. What is the pilot send limit? Suggested: 50/day.

---

## Acceptance Criteria for MVP

- Hermes can create a WhatsApp approval request for an allowlisted contact.
- Esa can approve or reject the request via Telegram/manual command.
- Approved utility template sends through Meta Cloud API.
- Message is logged in Supabase with Meta message ID.
- Non-allowlisted contacts cannot be messaged.
- Inbound WhatsApp replies are received through the Cloudflare Tunnel webhook.
- Inbound replies are stored in Supabase.
- Conversation window is updated after inbound reply.
- Free-form confirmation reply can be sent inside the open window after policy check.
- All secrets remain local and are not printed in logs or chat.

---

## Suggested First Implementation Command

When starting in a fresh session, tell Hermes:

```text
Load the plan at docs/plans/2026-05-16-whatsapp-communication-implementation.md and implement Phase 1 only. Use subagent-driven-development, TDD, and keep all sends in dry-run mode until I explicitly approve live Meta API testing.
```
