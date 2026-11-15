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
| 12:30-1:15 | Backend: in Mel, run `cd backend && python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt && uvicorn main:app --reload --port 8000`. Then run `ngrok http 8000` and `python tools/make_tools.py https://<id>.ngrok-free.app`. Check `curl https://<id>.ngrok-free.app/health` |
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
1. **Status (EN):** "Hey, it's Carlos, is my Malibu ready?" Bear checks the RO and says it's waiting on the wheel bearing, ready tomorrow by 3, around $480.
2. **Booking (ES):** "Hola, necesito cambiar los frenos de mi Altima el lunes a las nueve." Bear switches to Spanish and books it. **The row appears on /admin live.**
3. **Tow:** "My car's smoking on I-95 near exit 12." Bear asks about safety first and gives the 911 advice, then logs the tow, which shows up on /admin.
4. **Kreyòl:** "Bonjou, mwen bezwen yon estimasyon pou pòt machin mwen." Bear switches to French and takes a message flagged for a Kreyòl callback. Say: "Native Creole is next, and here's the plan."
5. **Transfer:** "Let me talk to the owner." Bear does a warm transfer to your phone, which rings in your pocket. Big finish.

Reset between runs: `curl -X POST localhost:8000/admin/reset`

## Files
- `backend/main.py`, `backend/requirements.txt`: FastAPI tool server with a mock data store (`data.json` is created on first run)
- `tools/*.json`: ElevenLabs webhook tool configs. `make_tools.py` regenerates them with your ngrok URL, and `create_tools_via_api.py` is an optional API upload (not tested, since there was no key)
- `agent/system_prompt.md`: persona, language rules, the 6 call flows, guardrails, and first messages in EN/ES/FR/HT
- `agent/agent_config_notes.md`: settings, voice picking, Voice Design prompts, transfer rule

## To Do
ElevenLabs account and API key (claim the hackathon credits) · Mel account (required for judging) · Twilio account, number purchase, and SID/token or API key · ngrok account and authtoken (`ngrok config add-authtoken …`) · a GitHub repo · your cell number for transfers · the shop owner's OK before using any real shop name or data · a native Kreyòl speaker to proofread the HT lines.
Optional backend security: `export TOOL_SECRET=somethinglong`, then add an `X-Tool-Secret` secret header to each tool.
Compliance note for real deployment: Florida requires all-party consent to record calls (Fla. Stat. 934.03), which is why the greeting says "calls may be recorded." Bear also tells callers it's an AI.
