# Bear: a multilingual AI phone receptionist for auto repair & body shops
Hackathon starter kit · Miami AI Hackathon (Mel × ElevenLabs) · Sat Nov 14, 2026 · Miami Dade College Wolfson, 300 NE 2nd Ave, Bldg 2, 1st floor

**The problem:** shop owners miss calls with their hands in an engine. Each missed call is a lost job, and half the calls that do get answered are just "is my car ready?"
**What Bear does:** answers 24/7 in English, Spanish, and French (with a Kreyòl fallback, see below). It books drop-offs, checks repair status, logs tows, and takes messages, and it warm-transfers to the owner when needed.

## ⚠️ Hackathon rules that matter (from the Luma page)
- **Everything you build during the event has to be done in Mel** ("The Agentic Terminal"). Mel and agentic execution are **30%** of the score. Existing projects are allowed, but anything you develop during the hackathon must go through Mel, and you submit Mel session history or screenshots. Treat this kit as your pre-existing starting point, say so in your submission, and make every change today in Mel.
- **Deadline conflict:** Luma says **6:00 PM ET**, but globalaihackathon.com says 8:00 PM ET. **Plan for 6:00 PM.** Demos run 6:00-7:30 PM, winners at 7:30 PM.
- **To submit, you need:** a working prototype, a GitHub repo link, Mel history/screenshots, a description of the problem and how you used Mel, and a live demo or recording. Submit at https://www.globalaihackathon.com/submissions
- **Credits:** one month of ElevenLabs Creator (131k credits) plus Mel Builder credits for participants. The "Best Use of ElevenLabs" prize is 3 months of Scale.

## Architecture
```
Caller ──PSTN──> Twilio number ──(ElevenLabs native Twilio integration)──> ElevenLabs Agent "Bear"
                                                     │  STT → LLM → TTS, language_detection, transfer_to_number
                                                     │  webhook tools (HTTPS POST JSON)
                                                     ▼
                                   ngrok ──> FastAPI backend (backend/main.py)
                                              /tools/book_appointment  /tools/check_repair_status
                                              /tools/request_tow       /tools/take_message
                                              /admin  (live board, auto-refresh, for the demo screen)
                                              data.json (mock DB: 4 cars at the shop)
```
- **Phone options:** (a) **Twilio number imported into ElevenLabs.** The import auto-configures the number, and warm transfer works. This is the recommended path, about $1-2/mo for the number plus usage. (b) An ElevenLabs-native or SIP number if your account offers one. (c) No phone at all: use the agent's web test call or widget. **Keep (c) ready as a backup** in case venue Wi-Fi or Twilio trial limits cause problems.
- **Twilio trial caveat:** trial accounts play a trial message and can usually only call verified numbers. Upgrade with about $20, or demo inbound from your verified cell.

## Build plan (6 hours, roughly 12:00-6:00)
| Time | Task |
|---|---|
| 12:00-12:30 | Check in, claim ElevenLabs Creator and Mel credits. Create the GitHub repo and push this kit **through Mel** so your first commit shows up in Mel history |
| 12:30-1:15 | Backend: see **Run the backend locally** below (`uvicorn --factory main:create_app`, with `TOOL_SECRET` and `ADMIN_PASSWORD` set). Then run `ngrok http 8000` and `python tools/make_tools.py https://<id>.ngrok-free.app`. Check `curl https://<id>.ngrok-free.app/health` |
| 1:15-2:15 | ElevenLabs: create the agent (Blank) and paste `agent/system_prompt.md`. Add the first message, add the es and fr languages with their first messages, and turn on the language_detection and end_call tools. Add the 4 webhook tools from `tools/*.json` (or `ELEVENLABS_API_KEY=... python tools/create_tools_via_api.py`). Set the timezone to America/New_York. Use **Test AI agent** in the browser |
| 2:15-3:00 | Voices: audition library voices or use Voice Design (`agent/agent_config_notes.md`). Assign the EN gruff voice and the es/fr voices |
| 3:00-3:30 | Lunch (Gratitude Luncheon). Network, and ask the ElevenLabs folks about Kreyòl |
| 3:30-4:15 | Phone: Twilio > buy a 305/954 number > ElevenLabs Agents > Phone Numbers > Import (SID + token, or an API key SK…/secret) > assign Bear. Add transfer_to_number (warm) to your cell. Call it |
| 4:15-5:15 | Polish: test all 6 flows in EN and ES plus the Kreyòl fallback. Tighten the prompt and fix tool params. Optional stretch: Kreyòl greeting audio (below) |
| 5:15-5:45 | Record a 2-min backup video of a call with the /admin board on screen. Write the submission blurb, export Mel screenshots |
| 5:45-6:00 | **Submit.** Rehearse the pitch |

## Haitian Creole: the honest answer
- **ElevenLabs Agents doesn't support Haitian Creole right now** (checked Nov 14, 2026). Agents can use every language their TTS model supports, and new agents default to Eleven v4 Turbo. The Eleven v4 list (90+ languages), the v3 list, and the Scribe speech-to-text list all include French and Spanish but **not Haitian Creole**.
  - https://elevenlabs.io/docs/help-center/product/eleven-agents/which-languages-can-i-use-with-eleven-agents
  - https://elevenlabs.io/docs/overview/capabilities/text-to-speech/eleven-v4
  - https://elevenlabs.io/docs/help-center/other/what-languages-do-you-support
  - https://elevenlabs.io/docs/overview/capabilities/speech-to-text (Supported languages section)
- **Fallback for the hackathon (what this kit does):** if a caller speaks Kreyòl, Bear switches to **French**, which ElevenLabs supports and many Haitian callers understand. It opens with one short Kreyòl line, then offers French, English, or Spanish, or takes a message flagged `callback_language: "ht"` so a Kreyòl-speaking person calls back. Pitch this as "French now, native Kreyòl on the roadmap."
- **Stretch: real Kreyòl audio.** Google Cloud Text-to-Speech lists **Haitian Creole (Haiti), ht-HT**: https://docs.cloud.google.com/text-to-speech/docs/list-voices-and-types. Meta's open model **facebook/mms-tts-hat** also works: https://huggingface.co/facebook/mms-tts-hat. You can generate a Kreyòl greeting or IVR prompt ("Peze 3 pou Kreyòl") as audio and play it in a Twilio IVR in front of the agent.
- **Roadmap: a full Kreyòl voice agent** would be a custom pipeline. Twilio Media Streams feeds a Kreyòl-capable STT (AssemblyAI advertises Haitian Creole: https://www.assemblyai.com/languages/haitian-creole; I haven't tested it), then an LLM, then Google ht-HT TTS. That's too much to build in a single afternoon.
- Quick experiment: ElevenLabs TTS reading Kreyòl text with a French voice might sound passable, since Kreyòl spelling is phonetic, but it isn't supported. Test it before showing it to anyone.
- Note: the Language doc says a language picked in the widget stays fixed for the call, while the language_detection tool switches languages based on what the caller says. Test switching on a real phone call.

## Demo script (90 seconds, live call on speaker plus /admin on the projector)
1. **Status (EN):** "Hey, it's Carlos Rivera, is my Malibu ready? The number on the ticket is 305-555-0101." Bear verifies last name + phone (or RO 1001) and says it's waiting on the wheel bearing, ready tomorrow by 3, around $480. Last name alone is not enough; Bear will ask for the phone or RO number.
2. **Booking (ES):** "Hola, necesito cambiar los frenos de mi Altima el lunes a las nueve." Bear switches to Spanish and books it. **The row appears on /admin live.**
3. **Tow:** "My car's smoking on I-95 near exit 12." Bear asks about safety first and gives the 911 advice, then logs the tow, which shows up on /admin.
4. **Kreyòl:** "Bonjou, mwen bezwen yon estimasyon pou pòt machin mwen." Bear switches to French and takes a message flagged for a Kreyòl callback. Say: "Native Creole is next, and here's the plan."
5. **Transfer:** "Let me talk to the owner." Bear does a warm transfer to your phone, which rings in your pocket. Big finish.

Reset between runs (only when `BEAR_ENV=dev`): `curl -u "admin:$ADMIN_PASSWORD" -X POST localhost:8000/admin/reset`

## Files
- `backend/main.py`, `backend/requirements.txt`, `backend/requirements-dev.txt`: FastAPI tool server (`create_app()` factory) with a mock data store (`data.json` is created on first run)
- `tests/`: pytest suite (auth, admin escaping, repair-status verification, tool behaviour, tool scripts)
- `.env.example`: every environment variable with placeholder values
- `tools/*.json`: ElevenLabs webhook tool configs. `make_tools.py` regenerates them with your ngrok URL, and `create_tools_via_api.py` is an optional API upload (not tested, since there was no key)
- `agent/system_prompt.md`: persona, language rules, the 6 call flows, guardrails, and first messages in EN/ES/FR/HT
- `agent/agent_config_notes.md`: settings, voice picking, Voice Design prompts, transfer rule

## To Do
ElevenLabs account and API key (claim the hackathon credits) · Mel account (required for judging) · Twilio account, number purchase, and SID/token or API key · ngrok account and authtoken (`ngrok config add-authtoken …`) · a GitHub repo · your cell number for transfers · the shop owner's OK before using any real shop name or data · a native Kreyòl speaker to proofread the HT lines.
Backend security is **required**, not optional: the backend refuses to start without a `TOOL_SECRET` of 32+ characters (unless `BEAR_ENV=dev`), and each ElevenLabs webhook tool must send it as an `X-Tool-Secret` **Secret** header.

## Run the backend locally
Requires Python 3.11+ (tested on 3.13).

```bash
python -m venv .venv && . .venv/bin/activate
pip install --require-hashes -r backend/requirements-dev.lock   # pinned runtime + dev deps
cp .env.example .env                               # then replace both CHANGE_ME values; never commit .env
set -a && . ./.env && set +a                       # load the variables into this shell
cd backend && uvicorn --factory main:create_app --reload --port 8000 --no-access-log
```

Installs use the hashed lockfiles (`backend/requirements.lock` for runtime, `backend/requirements-dev.lock` for dev). After you change `backend/requirements.txt` or `backend/requirements-dev.txt`, refresh the pins:

```bash
pip-compile --allow-unsafe --generate-hashes --no-strip-extras --output-file=backend/requirements.lock backend/requirements.txt
pip-compile --allow-unsafe --generate-hashes --no-strip-extras --output-file=backend/requirements-dev.lock backend/requirements-dev.txt
```


`main:app` no longer exists. The app is built by the `create_app()` factory, which reads settings when it starts. With the `CHANGE_ME` placeholder still in place it refuses to start (the secret is too short), which is on purpose.

### Environment
| Variable | Required | Default | What it does |
|---|---|---|---|
| `TOOL_SECRET` | yes (unless `BEAR_ENV=dev`) | none | Shared secret every `/tools/*` call must send in `X-Tool-Secret`. Min 32 chars. Generate one with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. |
| `ADMIN_USER` | no | `admin` | HTTP Basic username for `/admin`. |
| `ADMIN_PASSWORD` | for `/admin` | none | HTTP Basic password. If unset, `/admin` returns 503. |
| `BEAR_ENV` | no | none | Set to `dev` for local hacking: allows starting without `TOOL_SECRET` (tools are then open, with a warning) and enables `POST /admin/reset`. Never set it in production. |
| `TOOL_DEADLINE_MS` | no | `4000` | Deadline in ms for `POST /tools/*` (positive integer; anything else stops startup). On timeout the tool returns `200 {"ok": false, "error": "timeout", ...}` and the abandoned worker can no longer save. `/health` and `/admin` have no deadline. |
| `DATA_FILE` | no | `backend/data.json` | Mock JSON data store (created with 4 demo repair orders on first use). Replaced by a real DB in PLAN Task 5. |
| `SHOP_NAME` | no | `Vice City Auto` | Shop name shown on the board and used as the default tow destination. |
| `SHOP_TZ` | no | `America/New_York` | Shop time zone for slots and timestamps. |

Endpoints: `GET /health` (public, returns only `ok` and `time`), `POST /tools/{book_appointment,check_repair_status,request_tow,take_message}` (need `X-Tool-Secret`), `GET /admin` (HTTP Basic), `POST /admin/reset` (dev only, HTTP Basic). Repair status needs the **last name plus the phone number or RO number** on the ticket.

### Tool responses and input rules
- Every tool reply has `ok`. Bad input comes back as `200 {"ok": false, "error": "invalid_input", "field": "...", "message": "<speakable hint>"}` so the agent can re-ask; an unexpected server error is `200 {"ok": false, "error": "internal_error", ...}`; a deadline miss is `error: "timeout"`.
- Phones are normalized to E.164 (US default, 10 or 11 digits). Dates are `YYYY-MM-DD` within 60 days; times `HH:MM`. Names max 80 chars, free text max 500; control characters are stripped.
- Before auth runs, `/tools/*` requires `Content-Type: application/json` (else 415), a body of at most 16 KB (else 413) and a JSON object (else 400).

### Logs
The app writes one JSON line per request to stdout (logger `bear.request`), e.g.
`{"request_id":"…","method":"POST","path":"/tools/take_message","tool":"take_message","conversation_id":"conv_smoke1","ts":"…+00:00","level":"info","event":"request","status":200,"latency_ms":16.9,"ok":true,"phone":"***0199"}`.
Only allowlisted fields are logged: phones as the last 4 digits, never names, vehicles, addresses, notes or other free text, no headers, query strings, bodies, exception messages or secrets. `X-Request-Id` (if it matches `[A-Za-z0-9._-]{1,64}`, otherwise generated) is echoed on every response; `X-Conversation-Id` is logged when it is valid. Run uvicorn with `--no-access-log`: its default access log prints client IPs and raw query strings.

## Run the tests
From the repo root, with the dev requirements installed:

```bash
python -m pytest                                   # whole suite (config in pyproject.toml)
python -m pytest --cov --cov-report=term-missing   # with coverage for backend/ and tools/
```

Lint and format (same pinned ruff as CI): `ruff check . && ruff format --check .`

### CI (GitHub Actions)
On every push and pull request:
- **tests**: pytest on Python 3.11 and 3.13 with `--cov-fail-under=80`; `coverage.xml` is uploaded as an artifact.
- **quality**: `ruff check` and `ruff format --check`.
- **security**:
  - CodeQL (security-and-quality queries; the SARIF file is kept as an artifact and summarized, and any error-level finding fails the job);
  - `pip-audit --strict` on the hashed lockfile;
  - gitleaks over the full git history.

Security also runs weekly. Dependabot opens weekly grouped updates for pip and the pinned actions. Because the repo is private, CodeQL results are not uploaded to the Security tab by default. Set the repo variable `CODE_SCANNING_UPLOAD=true` once code scanning is available.

The tests use only dummy secrets and temp data files. They never call the network or write into `tools/`.
Compliance note for real deployment: Florida requires all-party consent to record calls (Fla. Stat. 934.03), which is why the greeting says "calls may be recorded." Bear also tells callers it's an AI.
