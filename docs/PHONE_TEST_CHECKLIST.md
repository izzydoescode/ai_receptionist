# Phone test checklist (go-live)

Run this on a real cell phone against the real Twilio number, after `tools/rehearse.py` passes. Keep `/admin` open on a laptop and the staff alert inbox open on another screen. Use only the demo shop and made-up caller details.

## Before you dial
- [ ] `curl https://<host>/health` and `curl https://<host>/ready` both return ok.
- [ ] `python tools/rehearse.py` passes every case at least 2/3 (run it again if one is flaky; fix the prompt or tool description if it stays below that).
- [ ] The deployed shop file has the real `twilio_number`, your cell as `transfer_number`, real voice ids, and `alerts.email` set (`docs/DEPLOY.md`, "Real shop files").
- [ ] `ALERTS_MODE=email` is set and the startup log has no "lists no alerts.email recipients" warning.
- [ ] In ElevenLabs, `shop_id` and `caller_phone` show as dynamic variables (not LLM-filled) on the tools (`docs/ELEVENLABS_INIT.md` step 5).
- [ ] The demo seed is loaded (`fly ssh console -C "python -m bear.seed --demo"`) so RO 1001–1004 exist, or you created test ROs at `/admin/ros`.

## Calls

| # | Call | What to check |
|---|---|---|
| 1 | **EN booking.** "I need an oil change for my Accord next Monday at ten." Give a made-up name, confirm the number. | Greeting says the call may be recorded, before the first question, and doesn't call Bear an AI. Ask "am I talking to a real person?": Bear says it's the shop's AI assistant. Bear reads back the details before booking. The row appears on `/admin`. A "New booking" email arrives. |
| 2 | **EN status, caller ID.** Call from a phone whose number is on a test RO (create one at `/admin/ros` with your cell). Give only the last name. | Bear verifies without asking for the number and says the done-by time naturally ("tomorrow by 3 PM"). |
| 3 | **EN status, other phone.** "Is my car ready? Rivera." | Bear asks for the repair order number or the phone on the ticket. With "1001" it gives the status and ETA. |
| 4 | **Past-due ETA.** Edit a test RO's done-by time to an hour ago, then ask for its status. | Bear says it's running past the estimate, doesn't invent a new time, and offers to take a message. |
| 5 | **EN message.** "Have the owner call me about a body work estimate." | Message on `/admin`. Email arrives with name, full phone and reason. Bear promises a callback "as soon as possible" only. |
| 6 | **ES booking.** Speak Spanish from the first sentence. | Bear switches to Spanish, says the recording notice once in Spanish, books it, and `/admin` shows language `es`. |
| 7 | **ES status.** "¿Ya está listo mi carro? Gómez, orden 1004." | Spanish answer with the status. |
| 8 | **FR booking.** Speak French. | Bear switches to French, gives the recording notice in French, books with language `fr`. |
| 9 | **Kreyòl opener.** Start in Haitian Creole and ask for a Kreyòl callback. | Bear says the Kreyòl bridge line, continues in French, and takes a message with callback language `ht`. It promises a Kreyòl callback only if the shop lists `ht` in `callback_languages`. The email subject says "call back in Haitian Creole". |
| 10 | **Transfer.** Ask for a person twice during open hours. | Your cell rings (warm transfer) and you hear Bear's summary first. |
| 11 | **Tow, unsafe.** "My car's smoking on the highway and I'm still in it." | The 911 advice comes first. Tow logged, and the email subject says "CALLER MAY BE UNSAFE". |
| 12 | **Noise and barge-in.** Call from a car or with a TV on; interrupt Bear mid-sentence. | Bear stops talking when interrupted and recovers. |
| 13 | **Silence.** Say nothing for 10+ seconds after the greeting. | Bear prompts once or twice, then ends the call politely. |
| 14 | **Unknown number.** If you have a second Twilio number not in any shop file, call it. | Neutral greeting that only takes a message. The message lands on `/admin?shop=unknown` and emails the default shop's staff. |

After the calls, check that the post-call summaries show under "Recent calls" on `/admin`, and that no names, phone numbers or reasons appear in the server logs (`fly logs`): only kinds, shop ids, ticket ids and masked phones.

## Results

Date: ____  Agent version / prompt commit: ____  Tester: ____

| # | Result (pass / fail) | Notes (what Bear said, latency, anything odd) |
|---|---|---|
| 1 | | |
| 2 | | |
| 3 | | |
| 4 | | |
| 5 | | |
| 6 | | |
| 7 | | |
| 8 | | |
| 9 | | |
| 10 | | |
| 11 | | |
| 12 | | |
| 13 | | |
| 14 | | |

Native-speaker review of the ES, FR and HT lines (`docs/TRANSLATION_REVIEW.md`) is still needed before a real shop goes live; these test calls don't replace it.
