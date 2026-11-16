# Bear: hardening and pilot plan

Status: approved stack is **Twilio number → ElevenLabs Agents (native Twilio integration) → FastAPI tool backend**. Bland was evaluated and not chosen. Reasons: on Bland's cheap plans, SMS and warm transfer are gated, and the plan that has them allows only one call at a time.

Written 2026-11-14 by Porfirio. Every task below is sized for one Codex session: one focused change, tests included, reviewable in one sitting. Do them in order unless a task says it can run in parallel.

## Ground rules for every task
- Keep the made-up shop **Vice City Auto**. No real shop names, phone numbers, or customer data in the repo.
- No secrets in git. Read config from env vars and document them in `.env.example`.
- Every task adds or updates `pytest` tests and leaves `pytest -q` green. CI is out of scope until GitHub auth is approved.
- Tool endpoints must keep answering in a shape the voice agent can read aloud: always `200` with `{"ok": bool, "message": str, ...}` for business errors. Use 4xx only for auth and malformed transport (401/403/413/415).
- Don't push, deploy, buy numbers, or call paid APIs from a task. Mark anything that needs a live key as a manual step.

## Known issues in the imported MVP (baseline, commit 1)
1. `/admin` renders caller-supplied text unescaped (stored XSS, confirmed with `<script>` in `caller_name`).
2. `/admin` and `POST /admin/reset` have no auth.
3. `TOOL_SECRET` is optional, so the tool endpoints are open by default.
4. `check_repair_status` matches on last name alone and returns the repair note and balance. Verification is only a hint to the LLM.
5. Weak input validation: phone numbers, dates, enums and lengths aren't checked.
6. There are no timeouts and no logging.
7. Persistence is a JSON file with no locking, so concurrent calls can lose writes. It's also lost on ephemeral hosts.
8. Hours and bays are hard-coded in `main.py` and duplicated in the prompt.
9. The French first message has no recording notice and mixes in a Kreyòl phrase.
10. The prompt and tools promise things that don't exist: "text photos to the shop number" (no SMS), "dispatcher will call back within 15 minutes" (no dispatcher integration), and "the calendar works 24/7".
11. Tool URLs are `YOUR-NGROK-ID` placeholders, and `create_tools_via_api.py` has never been run.

---

## Phase A: Security

### Task 1: Test harness and lock down admin and tool auth (done on `fix/tool-auth-and-admin-xss`)
Scope: `backend/`, new `backend/tests/`, `.env.example`, README env section.
- Add `pytest` and `httpx` to `backend/requirements-dev.txt`, with `backend/tests/conftest.py` that points `DATA_FILE` at a tmp path.
- `TOOL_SECRET` is **required**. If it's unset or under 32 characters, the app refuses to start, unless `BEAR_ENV=dev`, where it logs a loud warning. Compare with `hmac.compare_digest`. The header stays `X-Tool-Secret`.
- `/admin` and `/admin/reset` require auth: HTTP Basic, with `ADMIN_USER`/`ADMIN_PASSWORD` from env and a constant-time compare. `/admin/reset` is also disabled unless `BEAR_ENV=dev`. `/health` stays public and must not leak config.
- All dynamic values on `/admin` are HTML-escaped (`html.escape`), including table headers and `SHOP_NAME`.
- Add `.env.example` listing every env var with a dummy value.

Acceptance:
- Tool POST without the header, or with a wrong secret → 401. Correct secret → 200.
- App startup with no `TOOL_SECRET` and `BEAR_ENV` unset → raises a clear error.
- `GET /admin` without credentials → 401 with `WWW-Authenticate`. With credentials → 200.
- A message with `caller_name="<script>alert(1)</script>"` appears on `/admin` as `&lt;script&gt;`, and raw `<script>alert(1)` is absent.
- `POST /admin/reset` returns 404 when `BEAR_ENV` isn't `dev`.
- If `ADMIN_PASSWORD` is unset, `/admin` returns 503 (admin disabled) rather than running open.
- Existing tool payloads and response fields are unchanged.

### Task 2: Repair-status verification (done on `fix/tool-auth-and-admin-xss`)
- New contract: `check_repair_status` requires `last_name` **and** one of `phone` or `ro_number`. Plate alone is no longer enough. *Decision at implementation: plate was dropped as a factor entirely (it's visible on the street, so it proves nothing); extra fields like `plate` are ignored, not rejected. Both responses stay without an `ok` field until Task 3 adds the common response shape.*
- If not verified, return `{"ok": true, "found": false, "message": "I couldn't verify that. I can take a message for the service writer."}`. Use the same response whether the record doesn't exist or the second factor is wrong, so callers can't probe for records.
- If verified, return status, ETA, first name and vehicle. The balance is only included when verified, always phrased as an estimate.
- Matching: case- and accent-insensitive last name (`unicodedata` NFKD), phone on its last 10 digits, exact RO. If more than one record matches, return none and take a message.
- Update `tools/make_tools.py` and the regenerated `tools/check_repair_status.json` to describe the two-factor rule. Update flow 2 in `agent/system_prompt.md`.

Acceptance: unit tests for each case:
- last name only → not found
- last name + wrong phone → not found
- last name + phone → found
- last name + RO → found
- RO only → not found
- accented name "Gómez" vs "gomez" → found
- the not-found response is byte-identical across failure modes

### Task 3: Input validation (done on `fix/input-validation-and-logging`)
- Phones: normalize to E.164 (US default). Reject anything that isn't 10 or 11 digits after normalizing. Default to the caller ID when it's provided.
- `preferred_date` is ISO and no more than 60 days out. `preferred_time` is HH:MM.
- Enums: `language ∈ {en, es, fr, ht}`, `urgency ∈ {low, normal, high}`.
- Max lengths: names 80, vehicle 80, free text 500. Strip control characters.
- Limit request bodies to 16 KB (413) and require `Content-Type: application/json` (415).
- Validation failures come back as `200 {"ok": false, "message": "<speakable hint>", "field": "..."}` so the agent can re-ask, not as a raw 422.

Acceptance: parametrized tests for each field, plus one oversized body and one wrong content type.

*Decisions at implementation: errors also carry `"error": "invalid_input"`; malformed or non-object JSON is 400; transport checks run before auth; JSON numbers for phone/RO are accepted. The caller-ID default for phone is **not** done yet (needs the ElevenLabs system variable wired into the tool config).*

### Task 4: Timeouts and structured logging (no PII or secrets) (done on `fix/input-validation-and-logging`)
- Middleware gives each request a deadline: `TOOL_DEADLINE_MS`, default 4000. On timeout, return `200 {"ok": false, "message": "Sorry, our system is slow right now. Let me take a message."}`.
- Lower `response_timeout_secs` in the tool configs from 20 to 10.
- JSON logs to stdout, one line per request: ts, request_id, tool, shop_id (once it exists), status, latency_ms, ok, and the ElevenLabs conversation id when present.
- Redaction: phones show only the last 4 digits. Names, free text, addresses and notes are **never** logged. Headers aren't logged. `X-Tool-Secret` and `Authorization` values never appear anywhere.

Acceptance:
- A test captures the log output of a `book_appointment` call. It contains `"tool":"book_appointment"` and `***0101`, and doesn't contain the name, the full phone number, or the secret.
- A test with a forced slow handler hits the timeout path.

*Decisions at implementation: tool exceptions return a safe `internal_error` envelope logged with `exc_type` only; a timed-out request's worker thread keeps running, but `save()` refuses to write after the deadline; `conversation_id` comes from an `X-Conversation-Id` header, which the ElevenLabs tool config still has to send (not yet verified); `shop_id` arrives with Task 7.*

## Phase B: Persistence

### Task 5: Storage layer (SQLite locally, Postgres-ready) (done on `dev/sqlite-postgres-persistence`)
- SQLAlchemy 2.x with Alembic, configured by `DATABASE_URL`. Default is `sqlite:///./vicecity.db`. A `postgresql+psycopg://` URL must work unchanged.
- Tables: `appointments`, `repair_orders`, `tows`, `messages`, `audit_events`. Every row has `shop_id`, UTC timestamps and a public id like `A1B2C3`.
- A repository module (`backend/bear/store.py`) holds all data access. Endpoints never touch the session directly.
- Booking is transactional: count and insert happen in one transaction, with a per-slot `UNIQUE(shop_id, slot_start, bay)` constraint so two concurrent bookings can't overbook.
- `python -m bear.seed --demo` loads the 4 demo repair orders for `vice-city-auto`. Remove `data.json` and its code path.

Acceptance:
- Tests run against SQLite.
- A test with 10 threads booking one slot that has 2 bays ends with exactly 2 bookings.
- `alembic upgrade head` works on an empty SQLite file.
- The README shows how to point at Postgres. Optional docker-compose Postgres test, marked `@pytest.mark.pg` and skipped by default.

### Task 6: Admin board on the DB, per shop (done on `dev/sqlite-postgres-persistence`)
- `/admin?shop=vice-city-auto` reads from the store and is still escaped and behind auth. It shows the newest 50 of each type, and timestamps in the shop's time zone.

Acceptance: snapshot-style test of the rendered table rows, and a test that shop A's rows don't appear on shop B's board.

*Decisions at implementation (Tasks 5-6, full contract in `docs/PERSISTENCE.md`):*
- *The demo shop is the made-up `vice-city-auto` (Vice City Auto). The default DB is `sqlite:///./vicecity.db`.*
- *The repository is `backend/bear/store.py`, with SQLAlchemy Core tables in `bear/db.py`.*
- *No overbooking: lowest free bay inside a savepoint, retried on `uq_appointments_shop_slot_bay` conflicts. A 10-thread test passes on SQLite (`BEGIN IMMEDIATE`) and Postgres 17.*
- *Public ids are a prefix plus 6 hex characters, with a retry on collision.*
- *Every write runs a `write_guard` just before commit, so a timed-out request rolls back.*
- *Audit events record only event, id and outcome, with no PII.*
- *The app never creates or seeds tables; run `alembic upgrade head` and optionally `python -m bear.seed --demo`.*
- *`/admin?shop=` uses one admin credential for every shop until per-shop auth exists. The shop time zone came from the global `SHOP_TZ` until Task 7, which replaced it with each shop's config.*

## Phase C: Per-shop config

### Task 7: Shop config schema and loader (done on `dev/per-shop-config`)
- One file per shop at `shops/<shop_id>.yaml`, validated by a Pydantic model and loaded at startup. The app fails fast on invalid config. `SHOPS_DIR` sets the location.
- Schema:
  - `shop_id`, `name`, `city`, `address`, `timezone`
  - `twilio_number` (E.164, unique across shops)
  - `transfer_number` (E.164)
  - `hours` per weekday, a list of `{open, close}`, with closed days as an empty list
  - `holidays` (dates)
  - `bays_per_slot`, `slot_minutes`
  - `services` (list of strings)
  - `languages` (subset of en/es/fr, plus `ht_fallback: message|french`)
  - `voices` (map of language → ElevenLabs voice_id)
  - `first_messages` (map of language → string, each must contain that language's recording notice)
  - `features`: `sms_confirmations: false`, `tow_dispatch: false`, `photo_texting: false`
  - `persona`: `name`, plus optional `style`
- Add `shops/vice-city-auto.yaml` as the example: made-up details, placeholder `+1305555xxxx` numbers, placeholder voice ids.
- Replace the hard-coded `OPEN_HOUR`, `SAT_*` and `BAYS_PER_SLOT` with config values. Every tool resolves `shop_id`.

Acceptance:
- Tests load the example.
- An invalid config (bad phone, duplicate twilio_number, close before open, missing recording notice) fails validation with a readable error.
- Slot logic follows the config hours, including a holiday.

*Decisions at implementation (full reference in `docs/SHOPS.md`):*
- *The schema is `bear.shops.ShopConfig`. Unknown keys are rejected. Times must be quoted `"HH:MM"`, because YAML 1.1 reads an unquoted `18:00` as an integer. All seven weekdays are required. `ht_fallback` is a top-level field, and `french` requires `fr` in `languages`.*
- *Recording notices are checked as substrings, ignoring case and accents: `may be recorded` / `puede ser grabada` / `peut être enregistré`. The example's French greeting gained its notice. `agent/system_prompt.md` is unchanged until Task 10.*
- *`load_shops()` reports every problem in every file at once. A file's `shop_id` must match its name. Error text never includes input values.*
- *Slots start at each interval's `open`. A requested time is floored to its slot, and a slot must end by `close`.*
- *`SHOP_NAME` and `SHOP_TZ` were removed (breaking). `SHOP_ID` is now the default shop and needs a file.*
- *Tools accept an optional `shop_id` body field. Unknown ids return `unknown_shop` and write nothing, not even audit rows. Task 8 still has to add `shop_id` to the tool configs as a dynamic variable and decide what an unknown number does.*
- *`/admin?shop=` with no shop file returns 404, and the board uses that shop's name and time zone. There is still one admin credential for all shops.*
- *PyYAML is now a runtime dependency.*

### Task 8: ElevenLabs conversation-initiation webhook (done on `dev/call-start-webhook`)
- `POST /elevenlabs/init` is protected by its own `INIT_WEBHOOK_SECRET` header (configured in ElevenLabs Agents settings).
- The request body has `caller_id`, `called_number`, `agent_id`, `call_sid` and `conversation_id`. Look up the shop by `called_number`.
- The response is `{"type": "conversation_initiation_client_data", "dynamic_variables": {...}, "conversation_config_override": {"agent": {"first_message": ..., "language": ...}, "tts": {"voice_id": ...}}}`.
- Dynamic variables: `shop_id`, `shop_name`, `shop_city`, `shop_address`, `hours_text` (generated from config), `services_text`, `persona_name`, `features_text`.
- An unknown number returns a generic, safe greeting with `shop_id="unknown"`. In that state, tools only take messages.
- The tool configs pass `shop_id` as a **dynamic-variable** body param, not something the LLM fills in. `make_tools.py` generates that.
- Respond in under 300 ms, because ElevenLabs fetches during the ring.
- Docs: https://elevenlabs.io/docs/agents-platform/customization/personalization/twilio-personalization and https://elevenlabs.io/docs/agents-platform/customization/personalization
- Manual step: enable "Fetch initiation client data from a webhook" and the first_message, language and voice overrides on the agent's Security tab.

Acceptance:
- Tests for a known number, an unknown number and a bad secret.
- Response JSON validates against a Pydantic model of the documented shape.
- Every dynamic variable used in `agent/system_prompt.md` is present in the response. A test parses the `{{...}}` placeholders and enforces this.

*Decisions at implementation (full reference in `docs/ELEVENLABS_INIT.md`):*
- *The header is `X-Init-Webhook-Secret`. `INIT_WEBHOOK_SECRET` is required outside dev (at least 32 characters, and different from `TOOL_SECRET`). This is a breaking change for deploys.*
- *`called_number` is normalized to E.164. Missing, unparseable or unmatched numbers all get the unknown greeting rather than an error, because a failed webhook can keep the call from starting.*
- *The language and voice come from the shop's first listed language. The unknown greeting is English, has a recording notice and no voice override.*
- *`hours_text` groups days that have the same hours and lists up to five upcoming holidays. `features_text` says plainly what the shop can't do.*
- *`shop_id` `unknown` is reserved. `take_message` files those messages under `unknown`, and they show at `/admin?shop=unknown`. The other tools return `unknown_shop`.*
- *The tool configs carry `shop_id` as `{"type": "string", "dynamic_variable": "shop_id"}` with no description, and it's required.*
- *The init log line has `shop_id` and the body's `conversation_id`, never the phone numbers.*
- *The ElevenLabs docs were checked on Nov 15, 2026. The request matches. Optional response fields (`prompt`, `branch_id`, `environment`, `user_id`) aren't used.*

### Task 9: Templated prompt and tool generation (done on `dev/templated-prompt-and-tools`)
- `agent/system_prompt.md` uses only dynamic variables for shop facts. No hard-coded hours.
- `make_tools.py` reads `PUBLIC_BASE_URL` and writes tool JSON with the secret header as a reference, never the value.
- Add `tools/README.md` describing the manual dashboard or API steps.

Acceptance:
- Grepping for `YOUR-NGROK-ID`, hard-coded hours or "Vice City" in the prompt finds nothing.
- Generated JSON round-trips, and a test diffs it against a fixture.

*Decisions at implementation:*
- *Three more init variables: `shop_timezone`, `languages_text` and `call_start_local`. `call_start_local` replaces `{{system__time}}` because that one follows the agent's single time zone and changes every turn, which breaks prompt caching. The agent now needs 11 placeholders.*
- *The prompt templates the persona name, city, hours, services, features, time zone and languages. Tests reject "Vice City", `YOUR-NGROK-ID`, a fixed time zone, clock times and weekday ranges. The persona's "South Florida patience" style line and the Kreyòl-to-French steps are still fixed text. Per-shop style and `ht_fallback` wording could come later.*
- *`make_tools.py` reads `PUBLIC_BASE_URL` (https, or http on localhost only). On disk, `X-Tool-Secret` is always the placeholder reference `{"secret_id": "YOUR-TOOL-SECRET-ID"}`. `create_tools_via_api.py` fills in `ELEVENLABS_TOOL_SECRET_ID` in memory at upload time and refuses an id equal to `TOOL_SECRET`. This came out of a CodeQL clear-text-storage alert: it means a mistakenly pasted secret is never written to a file. It no longer takes a command-line URL, which is a breaking change. Tools now also send `X-Conversation-Id` from `system__conversation_id`.*
- *The fixture is `tests/fixtures/tools/`. `create_tools_via_api.py` refuses to upload configs that still contain `YOUR-...` placeholders.*
- *Left for Task 10: the false promises (photos, 15-minute dispatch, 24/7 calendar) and the French first message.*

## Phase D: Prompt and content fixes (can run in parallel with Phase B)

### Task 10: Recording notice and no false promises (done on `dev/recording-notice-and-promises`)
- French first message: "Bonjour, vous êtes bien chez {{shop_name}}. Je suis {{persona_name}}, l'assistant virtuel. **L'appel peut être enregistré.** Comment puis-je vous aider ?" Remove the mixed-in Kreyòl phrase from the FR message; keep the Kreyòl handling in the flow instructions.
- Every first message, in config and in the prompt, carries its language's recording notice before anything substantive, per Fla. Stat. 934.03 (all-party consent).
- Remove the unbuilt promises, or gate them on `features_text`:
  - "text photos to the shop number" → only if `photo_texting`. Otherwise: "bring photos to the estimate, or the service writer will tell you where to send them."
  - "dispatcher will call back within 15 minutes" → only if `tow_dispatch`. Otherwise: "I've logged it and the shop will call you back as soon as possible. If you're unsafe, call 911."
  - "the calendar works 24/7" → "I can book you for the next open slot."
- `request_tow` and `take_message` response messages follow the same rules.
- Flag the ES, FR and HT lines for native-speaker review in a `docs/TRANSLATION_REVIEW.md` checklist.

Acceptance: a test checks that every `first_messages` value in each shop config contains its notice ("may be recorded", "puede ser grabada", "peut être enregistré", "ka anrejistre"). Grep tests confirm the banned phrases are gone when their features are off.

*Decisions at implementation:*
- *The per-feature wording lives in `features_text`: the real promise when a feature is on, and the exact fallback line when it is off. The prompt flows point at it, so a shop with features off never has "text photos", "15 minutes" or "24/7" in its rendered prompt. Tests render the prompt with the webhook's variables, once with every feature off and once with every feature on.*
- *`request_tow` promises the 15-minute dispatcher only with `tow_dispatch`. Otherwise it relays the fallback line, with the 911 warning still first for unsafe callers. `take_message` says "the shop will call back as soon as possible", with no time promise.*
- *Shop config validation already required each language's notice (Task 7). The new tests also check that the notice comes before the greeting's question, in `shops/*.yaml` and in the prompt's table (including the HT line, "ka anrejistre"). The demo shop's French greeting now uses the spec wording, "L'appel peut être enregistré".*
- *Follow-up (`dev/per-shop-callback-languages`): a per-shop `callback_languages` list (default `[en]`) now gates those promises. The Kreyòl bridge line comes from the `callback_text` dynamic variable and promises a Kreyòl-speaking callback only when `ht` is listed. `take_message` promises a language callback only for a listed language.*

## Phase E: Deployment

### Task 11: Containerize and write deploy docs (no deploy) (done on `dev/docker-and-fly-deploy`)
- Add a `Dockerfile`: slim Python, non-root user, `uvicorn --proxy-headers`, healthcheck on `/health`.
- Add `docs/DEPLOY.md` with the options below and env var lists. Run Alembic migrations on boot or as a release command.

| Option | Cost (checked 2026-11-14) | Fit | Tradeoffs |
|---|---|---|---|
| **Fly.io**, 1× shared-cpu-1x 256–512 MB in `iad`/`ewr`, plus **Neon** free Postgres | $2.19–3.69/mo; bills under $5 are waived once a card is on file. Neon free tier (1 GB/project, scales to zero) | **Recommended** | Always-on, so no cold start on the ElevenLabs init webhook. Needs a card on Fly. Neon cold start takes a few hundred ms after 5 min idle; warm it from `/health` or accept it. |
| **Render** web service plus Neon | Free tier spins down after 15 min idle, with about 1 min cold start. Paid Starter is about $7/mo (*price not re-verified*) | OK on paid only | The free cold start would make the init webhook and first tool call time out mid-ring. The free Render Postgres expires after 30 days, so use Neon. |
| **Netlify Functions** | Free tier | **Poor fit** | Netlify Functions only support JS, TS and Go, so the FastAPI backend would have to be rewritten. Serverless cold starts. |
| Vercel (Python functions) | Hobby tier is free | Not for pilots | Hobby plan is for non-commercial use. Serverless means no SQLite. |
| Supabase Postgres (instead of Neon) | Free, 500 MB, 2 projects | Fine | Free projects pause after a week of inactivity, which is risky for a quiet shop. |

Neon free tier: 100 projects, 100 CU-hours per project per month, 1 GB storage per project, scale-to-zero after 5 min that can't be disabled (https://neon.com/faqs/free-plan-limits-and-quotas). Other sources: https://docs.fly.io/about/pricing, https://docs.fly.io/about/free-waiver, https://render.com/docs/free, https://docs.netlify.com/build/functions/get-started, https://supabase.com/pricing.

Acceptance: `docker build` succeeds, `docker run` with env answers `/health`, and the docs list every required env var. **No account creation or deploy in this task.**

*Decisions at implementation:*
- *The image is two-stage on `python:3.13-slim-trixie`, pinned by digest, with Dependabot watching it. It runs as uid 10001 on port 8080, and its HEALTHCHECK uses only the standard library (the slim image has no curl). It defaults to SQLite at `/data/bear.db`.*
- *Migrations run as Fly's `release_command`, so a failed migration stops the deploy. The entrypoint can also migrate on boot (`BEAR_MIGRATE_ON_BOOT=1`) for a plain `docker run`.*
- *`fly.toml`: one always-on shared-cpu-1x/512 MB machine in `iad` (still under the $5 waiver). `FORWARDED_ALLOW_IPS=*` so `--proxy-headers` trusts Fly's proxy. Secrets only go through `fly secrets set`.*
- *Neon uses the direct connection string, not the pooler, to avoid PgBouncer quirks with psycopg prepared statements. `/health` doesn't query the database, so Neon's scale-to-zero stays on.*
- *A CI `container` job builds the image and smoke-tests it on SQLite and Postgres, because the box running these tasks has no Docker. Real shop YAML goes on a Fly volume via `SHOPS_DIR`, never in the repo.*

## Phase F: Scripted test-call suite (EN/ES)

### Task 12: Deterministic flow tests (done on `dev/scenario-call-tests`)
- `tests/scenarios/*.yaml`, one per flow and language: `en_status`, `es_status`, `en_book`, `es_book`, `en_book_full_slot`, `es_after_hours`, `en_tow_unsafe`, `es_tow_safe`, `en_message`, `es_message`, `en_transfer_request`, `en_unverified_status`.
- Each scenario lists the tool calls the agent should make with their arguments, and the expected response fields.
- A pytest runner replays them against the backend and checks responses, DB side effects and `/admin` rows.

Acceptance: 12 or more scenarios pass. Each runs in under 1 s. Spanish scenarios use Spanish free-text fields and check that `language="es"` is persisted.

*Decisions at implementation:*
- *The format is documented in `tests/scenarios/README.md`. Dates are placeholders like `{next_monday}`. Rows are matched as subsets on the shop's board, audit events are checked too, and `/admin` is checked for text.*
- *`en_transfer_request` replays the urgent `take_message` taken when a transfer fails or the shop is closed. The live transfer is an ElevenLabs system tool with no backend call.*
- *`es_after_hours` tries Sunday, gets three alternatives, then leaves a Spanish message. `es_status` checks an accent-insensitive last-name match.*
- *Not covered here (needs a key): whether the live agent produces these calls from speech. See Task 13.*

### Task 13: Live agent rehearsal script (manual, needs a key)
- `scripts/rehearse.py` uses the ElevenLabs agent testing or simulated-conversation API (*endpoint names to be confirmed against current docs at implementation time*). It plays each scenario's caller lines in text mode against the real agent and asserts which tools were called. Run each scenario 3 times, because routing is probabilistic.
- Add `docs/PHONE_TEST_CHECKLIST.md` for real calls: EN and ES on a cell phone, transfer rings the test cell, recording notice heard, the Kreyòl line goes to a callback message, background noise, barge-in, and silence for over 10 s.

Acceptance: the script runs with `--dry-run` and no key, the checklist exists, and a results table template is in the doc.

## Phase F2: Go-live features (Nov 15 plan)

### Task 16: Repair-order admin and spoken ETAs (done on `feat/repair-order-admin`)
- Staff create, edit and close repair orders at `/admin/ros` (behind the admin login, with a CSRF token tied to the shop and an Origin check). Every change writes `repair_order.created` / `repair_order.updated` to `audit_events`. Marking an order `picked_up` closes it, and closed orders stop answering status lookups.
- `repair_orders.eta_at` (migration 0005) holds the done-by time; the old `eta` text is kept as a note. `check_repair_status` says the time relative to the shop's day ("tomorrow by 3 PM") and adds `eta_past_due` and `estimate_updated`. A past-due or missing estimate tells the agent not to guess and to offer a message.
- `check_repair_status` takes `caller_phone` from `system__caller_id` as the phone factor when the caller didn't give one.
- *Later, not MVP:* this router (`bear/admin.py`) is where a dashboard (metrics from `call_summaries` and `audit_events`), customer and CRM views, and a paid estimator add-on would grow.

## Phase G: SMS confirmations (later, behind A2P 10DLC approval)

### Task 14: Twilio SMS confirmations behind a feature flag
Do this only after the A2P 10DLC brand and campaign are approved. Until then, `features.sms_confirmations` stays false and no code path sends SMS.
- `bear/sms.py` sends through the Twilio Messaging API from the shop's `twilio_number` (or its Messaging Service SID). Idempotent: one confirmation per appointment id, stored in `audit_events`. Bilingual templates per `language`.
- The agent asks permission on the call ("Want a text confirmation?"), and the consent flag is stored with the appointment.
- Handle STOP/HELP replies with an inbound webhook that has Twilio signature validation.
- Tests mock Twilio and check: no send when the flag is off, no send without consent, exactly one send on retries, and the STOP reply is honored.
- Cost: $0.0083 per segment plus about $0.003–0.005 carrier fee. Registration is $4.50 for the brand, $15 for campaign vetting, then $1.50–2/mo (https://www.twilio.com/en-us/sms/pricing/us, https://help.twilio.com/articles/1260803965530).

### Task 15 (optional): Post-call webhook ingestion (done on `dev/post-call-summary`)
- `POST /elevenlabs/post-call` verifies the ElevenLabs HMAC signature, stores the conversation id, duration, summary and outcome per shop, and shows them on `/admin`. Full transcripts and audio aren't stored by default; that follows the shop's retention choice.

*Decisions at implementation:*
- *The signature is checked without the SDK (`t=<secs>,v0=<hmac of "t.body">`): 30-minute maximum age like the SDK, plus a 5-minute future limit. The secret is `POST_CALL_WEBHOOK_SECRET`. If it's unset, the endpoint returns 503 and the app still starts; it must differ from the other secrets.*
- *Stored per call: conversation id (unique, so retries return `duplicate`), start time, duration, outcome, summary, title and language. The shop comes from the `shop_id` dynamic variable, or `unknown`. Audio, call-initiation failures and future event types get 200 and are ignored.*
- *A per-shop transcript retention option isn't built yet. No transcript, audio or phone number is stored; they stay in ElevenLabs under its retention settings. Fixtures follow the docs' example payloads. See `docs/ELEVENLABS_POST_CALL.md`.*

---

## Codex prompt for Task 1 (copy-paste)

```
You are working in the git repo at /workspace/repos/ai-receptionist (FastAPI backend in backend/main.py for an ElevenLabs voice agent called "Bear" for a made-up shop "Vice City Auto"). Read docs/PLAN.md, then implement ONLY "Task 1: Test harness + lock down admin and tool auth". Do not start other tasks.

Requirements:
1. Add backend/requirements-dev.txt (pytest, httpx) and backend/tests/ with conftest.py that sets DATA_FILE to a pytest tmp_path, sets TOOL_SECRET to a 40-char test value, ADMIN_USER/ADMIN_PASSWORD to test values, and builds a fresh app/TestClient per test (refactor main.py into a create_app()/settings-loading pattern if needed so env is read at app creation, not import time; keep `uvicorn main:app` working).
2. TOOL_SECRET is required: if unset or shorter than 32 chars, app creation raises a clear RuntimeError, unless BEAR_ENV=dev, in which case log a warning and allow it. Compare the X-Tool-Secret header with hmac.compare_digest. All four /tools/* endpoints depend on it.
3. Protect GET /admin and POST /admin/reset with HTTP Basic auth (ADMIN_USER/ADMIN_PASSWORD env; constant-time compare; 401 with WWW-Authenticate on failure). If ADMIN_PASSWORD is unset, /admin returns 503 "admin disabled". POST /admin/reset returns 404 unless BEAR_ENV=dev.
4. HTML-escape every dynamic value rendered by /admin (cells, headers, SHOP_NAME) with html.escape(..., quote=True).
5. /health stays public and returns only {"ok": true, "time": ...} (drop shop name).
6. Add .env.example at repo root listing TOOL_SECRET, ADMIN_USER, ADMIN_PASSWORD, BEAR_ENV, SHOP_NAME, SHOP_TZ, DATA_FILE with dummy values, and a short "Environment" section in README.md.
7. Do NOT change tool request/response field names or the tools/*.json files. Do not add new runtime dependencies (use the stdlib and what FastAPI already provides).

Tests that must exist and pass (pytest -q from backend/):
- tool POST with no header -> 401; wrong secret -> 401; correct secret -> 200 for each of the 4 tools (use valid payloads; book a slot on the next weekday at 10:00 shop time).
- app creation without TOOL_SECRET and BEAR_ENV unset -> RuntimeError; with BEAR_ENV=dev -> starts.
- GET /admin without auth -> 401 + WWW-Authenticate; with auth -> 200.
- take_message with caller_name "<script>alert(1)</script>" then GET /admin: body contains "&lt;script&gt;" and not "<script>alert(1)".
- POST /admin/reset without BEAR_ENV=dev -> 404.

Constraints: no network calls, no new accounts, don't push. Keep changes minimal and readable. When done, run pytest -q, show the output, and make one git commit with message "Task 1: require tool secret, admin auth, escape admin board, add tests" using author "Porfirio <porfirio@local>" unless the repo config already sets an author. Summarize what changed and anything you couldn't do.
```

## Decisions and approvals needed from Israel
1. **Host:** Fly.io plus Neon (recommended: about $0–4/mo, needs a card on Fly), Render paid plus Neon (about $7/mo), or something else. Netlify is a poor fit for this Python backend.
2. **Twilio:** upgrade from trial (about $20 prepaid) and buy one local number ($1.15/mo) for testing. Pick the area code (305/786 or 954).
3. **ElevenLabs plan after the hackathon credits run out:** Creator at $22/mo (275 min included, then $0.08/min) for the pilot, or Pro at $99/mo once both shops are live.
4. **A2P 10DLC for SMS (Phase G):** whose business registers (Israel's LLC or each shop's tax ID), about $20 one-time plus $1.50–2/mo per campaign. This can wait until after the pilot starts.
5. **GitHub:** approve auth to push this repo to `github.com/izzydoescode`.
6. Native ES, FR and HT speakers to review the scripts. The shop owner's OK before using any real shop name or data.
