# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

"Bear" is a multilingual AI phone receptionist for auto repair shops. It speaks English, Spanish and French, and Kreyòl callers are moved to French or leave a message. Calls flow Twilio number → ElevenLabs Agent (STT/LLM/TTS, configured in the ElevenLabs dashboard) → this FastAPI backend, which serves:
- a call-start webhook that picks the shop and greeting
- four webhook tools
- a post-call webhook
- a staff admin

Callers can book drop-offs, check repair status and ETA, request tows, and leave messages. `docs/PLAN.md` holds the roadmap and the decisions behind each task.

## Commands

```bash
python -m venv .venv && . .venv/bin/activate
pip install --require-hashes -r backend/requirements-dev.lock   # hashed lockfile, same as CI

python -m pytest                                    # whole suite, from the repo root (pyproject puts backend/, tools/ on sys.path)
python -m pytest tests/test_eta.py::test_estimate_age
python -m pytest tests/test_scenarios.py -v         # scripted call flows (tests/scenarios/*.yaml)
python -m pytest --cov --cov-fail-under=80          # CI's coverage gate
BEAR_TEST_PG_URL=postgresql+psycopg://... python -m pytest -m pg   # Postgres-only tests (skipped otherwise)
ruff check . && ruff format --check .               # CI's quality job (line length 120)

# Run locally: migrate, seed demo ROs, serve (needs TOOL_SECRET/INIT_WEBHOOK_SECRET >= 32 chars unless BEAR_ENV=dev)
cd backend && alembic upgrade head && python -m bear.seed --demo
uvicorn --factory main:create_app --reload --port 8000 --no-access-log

PUBLIC_BASE_URL=https://<host> python tools/make_tools.py   # regenerate tools/*.json
python tools/rehearse.py --dry-run                          # live-agent rehearsal (needs ELEVENLABS_* for a real run)
```

Dependencies: edit `backend/requirements*.txt`, then regenerate the `.lock` files with the `pip-compile --allow-unsafe --generate-hashes --no-strip-extras ...` command in `.github/workflows/quality.yml`. CI fails if the locks drift.

## Architecture

- **`backend/main.py`: `create_app()` factory, with no module-level app.**
  - It reads `Settings.from_env()`, loads and validates every shop YAML, checks the DB schema, and wires the routes as closures over `settings`, `store`, `shops` and `alerter`.
  - Startup fails loudly on bad config: short or shared secrets, an invalid shop file, a `sqlite:` URL outside dev, email alerts without a key.
  - Tests build a fresh app per test with `make_client(**env, _shops=(overrides...))` in `tests/conftest.py`. That fixture defaults to `BEAR_ENV=dev`, a temp SQLite database and the seeded demo shop.
- **`backend/bear/` modules:**
  - `shops.py`: strict pydantic `ShopConfig` (hours, slots, languages, voices, features, `callback_languages`, `alerts`) plus the text the agent is told.
  - `store.py`: transaction-scoped SQLAlchemy Core. SQLite uses `BEGIN IMMEDIATE` for writes, and the native upsert works on both SQLite and Postgres. `db.py` holds the schema. `backend/migrations/` is Alembic, and `tests/test_migrations.py` fails if `db.py` and the migrations drift.
  - `initiation.py`: the `/elevenlabs/init` response.
  - `post_call.py` and `webhook_signature.py`: the HMAC-verified `/elevenlabs/post-call`.
  - `admin.py`: the `/admin/ros` router.
  - `alerts.py`: staff alerts.
  - `eta.py`: spoken ETAs.
  - `validation.py`: shared input cleaning and field types.
- **Multi-shop:**
  - The init webhook finds the shop by `called_number` and returns 12 dynamic variables. The agent must define exactly these.
  - Tools receive `shop_id` from a dynamic variable (`{"dynamic_variable": "shop_id"}`, with no description, so the LLM never sees it). A missing `shop_id` is rejected outside single-shop dev.
  - Numbers no shop owns map to `shop_id: unknown`, which can only take messages.
- **Middleware (raw ASGI):**
  - `ToolTransportMiddleware` checks `X-Tool-Secret` first, then enforces JSON, a 16 KB body cap and an object body on `/tools/*`.
  - `RequestLoggingMiddleware` puts a `TOOL_DEADLINE_MS` deadline on tool calls. A late worker is blocked from committing through the `_REQUEST_BOUND` contextvar and the store's `write_guard`. It writes one allowlisted JSON log line per request.
- **Tool response contract:** these replies are read aloud by the voice agent. Business errors, bad input, timeouts and internal errors are **HTTP 200** with `{"ok": false, "error": ..., "message": "<speakable>"}`, where `invalid_input` also carries `field`. Use 4xx only for auth and transport. Messages are instructions or lines for the agent, and every promise in them must be true for the shop (gated by `features` and `callback_languages`).
- **Privacy in logs:** never log names, free text, bodies, headers or secrets. Phones are masked to the last 4 digits. Staff alert log lines carry only the kind, shop and ticket id; caller details go only in the delivered email.
- **Repair orders:** staff manage them at `/admin/ros` (Basic auth, plus a CSRF token tied to the shop). `eta_at` is spoken relative to the shop's day. `picked_up` closes an order. `check_repair_status` requires a last name plus a phone (or caller ID) or RO number, and failures are byte-identical.

## Generated and checked files

- `tools/*.json` come from `tools/make_tools.py`.
  - `tests/test_tool_scripts.py` diffs them against `tests/fixtures/tools/` (generated with `PUBLIC_BASE_URL=https://bear.example.test`) and against the committed placeholder output.
  - After editing `TOOLS`, regenerate both: run `make_tools.py` with no URL for `tools/`, and with the fixture URL for the fixtures.
  - The secret header is always the placeholder `{"secret_id": "YOUR-TOOL-SECRET-ID"}` on disk. `create_tools_via_api.py` fills it in at upload time.
- `agent/system_prompt.md` must take every shop fact from `{{dynamic_variables}}`. `tests/test_prompt.py` rejects hard-coded shop names, times, time zones and `{{system__time}}`, requires the recording notice in every greeting, and forbids an AI self-introduction (Bear says it is an AI only when asked, and never claims to be human).
- `docs/TRANSLATION_REVIEW.md` lists every ES, FR and HT line that needs native-speaker sign-off, and a test keeps it in sync.
- `docs/DEPLOY.md` must list every env var the backend reads (`tests/test_deploy_files.py`).

## Ground rules (docs/PLAN.md)

- Keep the made-up shop **Vice City Auto** and 555-01XX numbers. No real shop names, phone numbers, addresses or emails in git. Real shop files live only on the deployed volume.
- Every config value comes from an env var documented in `.env.example`, the README table and `docs/DEPLOY.md`.
- Every change adds or updates tests and keeps `pytest`, ruff and the 80% coverage gate green. Tests never hit the network.
- Don't deploy, buy numbers or call paid APIs (ElevenLabs, Twilio, Resend) from a task. Anything that needs a live key is a manual step.
