# Translation review checklist

Every Spanish (ES), French (FR) and Haitian Creole (HT) line a caller can hear needs a native speaker to sign off before it's used on real calls. Check that each line is correct, natural on the phone, and says no more than the shop actually offers. A recording notice has to come before anything substantive (Fla. Stat. 934.03).

**Machine check (Nov 15, 2026):** the FR and HT lines (rows 2, 3 and 5a) were back-translated with Google Translate, Microsoft Translator (Bing / DuckDuckGo), MyMemory and Yandex. The FR greeting and the Kreyòl bridge line held up. The HT greeting used to open with *ou rele {{shop_name}}*, which also reads as "your name is {{shop_name}}". It now says *ou sou liy {{shop_name}}* ("you're on the {{shop_name}} line"), which all four services back-translated cleanly. Row 5b is newer and wasn't part of that check. **A machine check is not a sign-off:** every FR and HT line still needs a native speaker.

For each line, record the reviewer's initials and the date in the last column. If you change a line, update every place it appears and clear the sign-off.

## Greetings (first messages)
| # | Lang | Where | Line | Check | Signed off |
|---|---|---|---|---|---|
| 1 | ES | `shops/*.yaml` `first_messages.es`; `agent/system_prompt.md` table | Gracias por llamar a {{shop_name}}. Le habla {{persona_name}}, el asistente virtual del taller. La llamada puede ser grabada. ¿En qué le puedo ayudar? | Notice "puede ser grabada" comes before the question; "usted" register | |
| 2 | FR | `shops/*.yaml` `first_messages.fr`; prompt table | Bonjour, vous êtes bien chez {{shop_name}}. Je suis {{persona_name}}, l'assistant virtuel. L'appel peut être enregistré. Comment puis-je vous aider ? | Notice "peut être enregistré" comes before the question; easy for Haitian callers to follow | |
| 3 | HT | prompt table (pre-recorded or external TTS only) | Bonjou, ou sou liy {{shop_name}}. Mwen se {{persona_name}}, asistan vityèl garaj la. Apèl la ka anrejistre. Kijan mwen ka ede w jodi a? | Notice "ka anrejistre" comes before the question; spelling | |
| 4 | ES (in the EN greeting) | `first_messages.en`; prompt table | Para español, solo hable en español. | Sounds natural as a short aside | |

## In-call lines
| # | Lang | Where | Line | Check | Signed off |
|---|---|---|---|---|---|
| 5a | HT | `backend/bear/shops.py` `KREYOL_BRIDGE_STAFFED`, sent in `callback_text` when the shop lists `ht` in `callback_languages` | Bonjou! Mwen pa pale Kreyòl byen. Nou ka pale franse, anglè oswa panyòl, oswa mwen pran mesaj ou epi yon moun ki pale Kreyòl ap rele w tounen. | Spelling and tone. Promises "someone who speaks Kreyòl will call you back", so it's only used when `ht` is listed. | |
| 5b | HT | `backend/bear/shops.py` `KREYOL_BRIDGE_UNSTAFFED`, used otherwise | Bonjou! Mwen pa pale Kreyòl byen. Nou ka pale franse, anglè oswa panyòl, oswa mwen pran mesaj ou epi garaj la ap rele w tounen. | New line: "…or I take your message and the garage will call you back." Check that it reads naturally and promises no language. | |
| 6 | ES | `backend/main.py` `STAFFED_CALLBACK`, the `take_message` reply (read by the agent) | Alguien que habla español le llamará. | Only sent when the shop lists `es` in `callback_languages`. | |
| 7 | ES | prompt, "Spanish" style notes | carro, goma (tire), frenos, aire acondicionado, chapistería/hojalatería | Words callers actually use in South Florida | |

## Agent-generated speech
Everything else the agent says in ES or FR is generated live from the English prompt. During review, run one Preview call in each language through all six flows. Listen for promises that aren't in the shop's features (photos by text, dispatch times, 24/7 booking), for the recording notice, and for how times and dollar amounts are spoken.
