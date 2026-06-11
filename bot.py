import os
import json
import re
import tempfile
import asyncio
import genanki
import random
from groq import Groq
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]

groq_client = Groq(api_key=GROQ_API_KEY)

message_buffer: dict[int, list[str]] = {}
pending_tasks: dict[int, asyncio.Task] = {}

PROMPT = """Ты эксперт по созданию Anki карточек для медицинских студентов. Твоя задача — создать МАКСИМАЛЬНО ДЕТАЛЬНЫЕ карточки, покрывающие ВЕСЬ текст.

Пользователь пришлёт текст. В начале могут быть:
Колода: [название]
Подколода: [название]

СТРАТЕГИЯ РАЗБИВКИ:
- Каждый раздел и подраздел → отдельная карточка
- Каждый вид/тип/форма заболевания → отдельная карточка
- Каждая линия терапии → отдельная карточка
- Инвазивные и неинвазивные методы → разные карточки
- Для большого текста 15-25 карточек — это норма

ФОРМАТ ВОПРОСА — конкретная тема с уточнением:
"H. pylori — факторы патогенности (подвижность, ферменты, токсины)"
"Антральный гастрит — клиника (время болей, характер, локализация)"
"Эрадикация H. pylori — 1-я линия (препараты + дозы мг/кг/сут)"
"Эрадикация H. pylori — 2-я линия (квадритерапия + дозы)"
"Верификация H. pylori — неинвазивные методы"
"Верификация H. pylori — инвазивные методы"
"рН-метрия — нормальные значения и интерпретация"
"Диетотерапия при гастрите — стол №1 и №5, что исключить"
"Антисекреторные препараты — ИПП (препараты, доза, режим приёма)"

ФОРМАТ ОТВЕТА — структурированный HTML с ПОЛНЫМИ деталями:
<b>Категория:</b>
<ul>
<li><b>Термин</b> — подробное пояснение, все цифры, дозы, сроки</li>
<li><b>Термин</b> — подробное пояснение, все цифры, дозы, сроки</li>
</ul>

КРИТИЧЕСКИ ВАЖНО:
1. Создай отдельную карточку для КАЖДОГО подраздела текста
2. КАЖДАЯ доза препарата (мг/кг/сут) — обязательно в ответе
3. КАЖДОЕ pH значение, срок лечения (дней), возрастное ограничение — в ответе
4. НЕ объединяй разные линии терапии в одну карточку
5. НЕ объединяй инвазивные и неинвазивные методы в одну карточку
6. НЕ объединяй разные формы гастрита (антральный, фундальный) в одну карточку
7. Только HTML: <b>, <ul>, <li>
8. Поле "deck" = колода::подколода от пользователя (если не указано — "Anki")
9. Верни ТОЛЬКО валидный JSON массив без пояснений:

[
  {
    "front": "Конкретный вопрос с уточнением темы",
    "back": "<b>Раздел:</b><ul><li><b>Термин</b> — детальное пояснение с цифрами</li></ul>",
    "deck": "Педиатрия::Гастроэнтерология"
  }
]"""

# CSS совпадает со стилем Basic модели в Anki
CARD_CSS = """
.card {
  font-family: Arial, sans-serif;
  font-size: 16px;
  text-align: left;
  color: #333;
  padding: 12px;
}
.front {
  font-weight: bold;
  font-size: 18px;
  color: #1a1a2e;
  margin-bottom: 8px;
}
b { color: #2c3e50; }
ul { margin: 5px 0; padding-left: 20px; }
li { margin: 3px 0; line-height: 1.6; }
hr { border: none; border-top: 1px solid #ddd; margin: 10px 0; }
"""

def parse_deck_name(text: str) -> str:
    deck = "Anki"
    subdeck = None
    for line in text.strip().split('\n'):
        line = line.strip()
        if line.lower().startswith("колода:"):
            deck = line.split(":", 1)[1].strip()
        elif line.lower().startswith("подколода:"):
            subdeck = line.split(":", 1)[1].strip()
    return f"{deck}::{subdeck}" if subdeck else deck

def create_apkg(cards: list, deck_name: str) -> str:
    model = genanki.Model(
        random.randrange(1 << 30, 1 << 31),
        "Medical Basic",
        fields=[{"name": "Front"}, {"name": "Back"}],
        templates=[{
            "name": "Card 1",
            "qfmt": "<div class='front'>{{Front}}</div>",
            "afmt": "<div class='front'>{{Front}}</div><hr>{{Back}}",
        }],
        css=CARD_CSS
    )

    deck = genanki.Deck(random.randrange(1 << 30, 1 << 31), deck_name)
    for card in cards:
        deck.add_note(genanki.Note(model=model, fields=[card["front"], card["back"]]))

    tmp = tempfile.NamedTemporaryFile(suffix=".apkg", delete=False)
    genanki.Package(deck).write_to_file(tmp.name)
    return tmp.name

async def process_buffered(chat_id: int, update: Update, context: ContextTypes.DEFAULT_TYPE):
    await asyncio.sleep(3)

    full_text = "\n".join(message_buffer.pop(chat_id, []))
    pending_tasks.pop(chat_id, None)

    if not full_text.strip():
        return

    deck_name = parse_deck_name(full_text)
    await update.message.reply_text(f"⏳ Генерирую карточки для «{deck_name}»...")

    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": PROMPT + "\n\nТекст:\n" + full_text}],
            temperature=0.2,
            max_tokens=32000,
        )
        raw = response.choices[0].message.content.strip()
        raw = re.sub(r"```json\s*", "", raw)
        raw = re.sub(r"```\s*", "", raw)

        cards = json.loads(raw)
        if not cards:
            await update.message.reply_text("❌ Карточки не созданы")
            return

        apkg_path = create_apkg(cards, deck_name)
        caption = (
            f"✅ {len(cards)} карточек\n"
            f"📚 {deck_name}\n\n"
            f"Открой файл в Anki → импортируй → синхронизируй"
        )
        with open(apkg_path, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=f"{deck_name.replace('::', '_')}.apkg",
                caption=caption
            )
        os.unlink(apkg_path)

    except json.JSONDecodeError as e:
        await update.message.reply_text(f"❌ Ошибка JSON: {e}\n\n{raw[:300]}")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if chat_id not in message_buffer:
        message_buffer[chat_id] = []
    message_buffer[chat_id].append(update.message.text)

    if chat_id in pending_tasks:
        pending_tasks[chat_id].cancel()

    pending_tasks[chat_id] = asyncio.create_task(
        process_buffered(chat_id, update, context)
    )

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Отправь текст:\n\n"
        "Колода: Онкология\n"
        "Подколода: Рак молочной железы\n\n"
        "[текст для карточек]\n\n"
        "Получишь .apkg файл — открой в Anki на телефоне.\n"
        "Можно отправлять несколькими сообщениями подряд."
    )

app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
print("Бот запущен...")
app.run_polling()
