# Translation review checklist

Every Spanish (ES), French (FR) and Haitian Creole (HT) line a caller can hear needs a native speaker to sign off before it's used on real calls. Check that each line is correct, natural on the phone, and says no more than the shop actually offers. A recording notice has to come before anything substantive (Fla. Stat. 934.03).

**Machine check (Nov 15, 2026):** the FR and HT lines (rows 2, 3 and 5a) were back-translated with Google Translate, Microsoft Translator (Bing / DuckDuckGo), MyMemory and Yandex. The FR greeting and the Kreyòl bridge line held up. The HT greeting used to open with *ou rele {{shop_name}}*, which also reads as "your name is {{shop_name}}". It now says *ou sou liy {{shop_name}}* ("you're on the {{shop_name}} line"), which all four services back-translated cleanly. Row 5b is newer and wasn't part of that check. The greetings in rows 1–3 were later rephrased to sound more casual and to drop the "virtual assistant" title; the recording notice is still required, and those new FR and HT wordings still need a native-speaker check. **A machine check is not a sign-off:** every FR and HT line still needs a native speaker.

For each line, record the reviewer's initials and the date in the last column. If you change a line, update every place it appears and clear the sign-off.

## Greetings (first messages)
| # | Lang | Where | Line | Check | Signed off |
|---|---|---|---|---|---|
| 1 | ES | `shops/*.yaml` `first_messages.es`; `agent/system_prompt.md` table | Gracias por llamar a {{shop_name}}, le habla {{persona_name}}. Solo para que sepa, esta llamada puede ser grabada. ¿En qué le puedo ayudar? | Notice "puede ser grabada" comes before the question; casual "solo para que sepa"; no AI self-introduction; "usted" register | |
| 2 | FR | `shops/*.yaml` `first_messages.fr`; prompt table | Bonjour, vous êtes bien chez {{shop_name}}, je suis {{persona_name}}. Juste pour vous le dire, l'appel peut être enregistré. Comment puis-je vous aider ? | Notice "peut être enregistré" comes before the question; casual "juste pour vous le dire"; no AI self-introduction; easy for Haitian callers to follow | |
| 3 | HT | prompt table (pre-recorded or external TTS only) | Bonjou, ou sou liy {{shop_name}}, mwen se {{persona_name}}. Jis pou w konnen, apèl la ka anrejistre. Kijan mwen ka ede w jodi a? | Notice "ka anrejistre" comes before the question; casual "jis pou w konnen"; no AI self-introduction; spelling | |
| 4 | ES (in the EN greeting) | `first_messages.en`; prompt table | Para español, solo hable en español. | Sounds natural as a short aside | |

## In-call lines
| # | Lang | Where | Line | Check | Signed off |
|---|---|---|---|---|---|
| 5a | HT | `backend/bear/shops.py` `KREYOL_BRIDGE_STAFFED`, sent in `callback_text` when the shop lists `ht` in `callback_languages` | Bonjou! Mwen pa pale Kreyòl byen. Nou ka pale franse, anglè oswa panyòl, oswa mwen pran mesaj ou epi yon moun ki pale Kreyòl ap rele w tounen. | Spelling and tone. Promises "someone who speaks Kreyòl will call you back", so it's only used when `ht` is listed. | |
| 5b | HT | `backend/bear/shops.py` `KREYOL_BRIDGE_UNSTAFFED`, used otherwise | Bonjou! Mwen pa pale Kreyòl byen. Nou ka pale franse, anglè oswa panyòl, oswa mwen pran mesaj ou epi garaj la ap rele w tounen. | New line: "…or I take your message and the garage will call you back." Check that it reads naturally and promises no language. | |
| 6 | ES | `backend/main.py` `STAFFED_CALLBACK`, the `take_message` reply (read by the agent) | Alguien que habla español le devolverá la llamada. | Only sent when the shop lists `es` in `callback_languages`. Casual "call back" wording; "devolverá la llamada" not "llamará". | |
| 6b | FR | `backend/main.py` `STAFFED_CALLBACK` | Quelqu'un qui parle français vous rappellera. | Only sent when the shop lists `fr` in `callback_languages`. New line; needs a native-speaker check. | |
| 6c | HT | `backend/main.py` `STAFFED_CALLBACK` | Yon moun ki pale Kreyòl ap rele w tounen. | Only sent when the shop lists `ht`. Same callback wording as the staffed Kreyòl bridge line. | |
| 7 | ES | prompt, "Spanish" style notes | carro, goma (tire), frenos, aire acondicionado, chapistería/hojalatería | Words callers actually use in South Florida | |

## Agent-generated speech
Everything else the agent says in ES or FR is generated live from the English prompt. During review, run one Preview call in each language through all six flows. Listen for promises that aren't in the shop's features (photos by text, dispatch times, 24/7 booking), for the recording notice, and for how times and dollar amounts are spoken.
