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

PROMPT = """Ты эксперт по созданию Anki карточек для медицинских студентов.

Пользователь пришлёт текст. В начале могут быть:
Колода: [название]
Подколода: [название]

ФОРМАТ ВОПРОСА — широкая тема, объединяющая группу фактов:
"Муцинозная цистаденома яичника — характеристика, размеры, риск"
"RMI — формула, компоненты, интерпретация"
"Осложнения опухолей яичников — виды, частота, лечение"

ФОРМАТ ОТВЕТА — структурированный HTML:
<b>Категория:</b>
<ul>
<li><b>Термин</b> — пояснение, цифры, механизм</li>
<li><b>Термин</b> — пояснение, цифры, механизм</li>
</ul>

Если категорий несколько:
<b>Категория 1:</b>
<ul><li><b>Термин</b> — пояснение</li></ul>
<b>Категория 2:</b>
<ul><li><b>Термин</b> — пояснение</li></ul>

ОБЯЗАТЕЛЬНЫЕ ПРАВИЛА:
1. Одна карточка = логически связанная группа фактов, НЕ один факт
2. Охвати ВЕСЬ текст без исключений — каждый факт, цифра, термин
3. Все числа, проценты, критерии, стадии — в ответе
4. Только HTML теги: <b>, <ul>, <li>
5. Поле "deck" = точное название колоды::подколоды от пользователя (если не указано — "Anki")
6. Верни ТОЛЬКО JSON массив без пояснений:

[
  {
    "front": "Вопрос — тема карточки",
    "back": "<b>Раздел:</b><ul><li><b>Термин</b> — пояснение</li></ul>",
    "deck": "Онкология::Рак молочной железы"
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
            max_tokens=8000,
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
