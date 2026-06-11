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

# Буфер для склейки сообщений (chat_id -> список сообщений)
message_buffer: dict[int, list[str]] = {}
pending_tasks: dict[int, asyncio.Task] = {}

PROMPT = """Ты эксперт по созданию Anki карточек для медицинских студентов.

Пользователь пришлёт текст. В начале текста могут быть указаны колода и подколода в формате:
Колода: [название]
Подколода: [название]

ФОРМАТ КАРТОЧЕК:

Вопрос — широкая тема, объединяющая логически связанную группу фактов. Примеры:
"Муцинозная цистаденома яичника — характеристика, размеры, риск"
"Осложнения опухолей яичников — виды, частота, лечение"
"RMI — формула, компоненты, интерпретация"

Ответ — структурированный HTML с жирными терминами и списками:
<b>Категория 1:</b>
<ul>
<li><b>Термин</b> — пояснение, цифры, механизм</li>
<li><b>Термин</b> — пояснение, цифры, механизм</li>
</ul>
<b>Категория 2:</b>
<ul>
<li><b>Термин</b> — пояснение</li>
</ul>

ПРАВИЛА:
1. Каждая карточка = логически связанная группа фактов (НЕ один факт на карточку!)
2. Охвати АБСОЛЮТНО ВЕСЬ текст — ни одна цифра, факт или термин не должен быть пропущен
3. Все цифры, проценты, критерии, механизмы — обязательно в ответе
4. Используй ТОЛЬКО HTML теги: <b>, <ul>, <li>
5. Верни ТОЛЬКО валидный JSON массив, никакого другого текста:

[
  {
    "question": "Тема карточки",
    "answer": "<b>Раздел:</b><ul><li><b>Термин</b> — пояснение</li></ul>"
  }
]"""

def parse_deck_name(text: str) -> tuple[str, str]:
    lines = text.strip().split('\n')
    deck = "Anki"
    subdeck = None
    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith("колода:"):
            deck = stripped.split(":", 1)[1].strip()
        elif stripped.lower().startswith("подколода:"):
            subdeck = stripped.split(":", 1)[1].strip()
    full_deck = f"{deck}::{subdeck}" if subdeck else deck
    return full_deck

def create_apkg(cards: list, deck_name: str) -> str:
    deck_id = random.randrange(1 << 30, 1 << 31)
    model_id = random.randrange(1 << 30, 1 << 31)

    anki_model = genanki.Model(
        model_id,
        "Medical Basic",
        fields=[{"name": "Question"}, {"name": "Answer"}],
        templates=[{
            "name": "Card 1",
            "qfmt": "<div class='question'>{{Question}}</div>",
            "afmt": "<div class='question'>{{Question}}</div><hr>{{Answer}}",
        }],
        css="""
.card { font-family: Arial, sans-serif; font-size: 16px; text-align: left; color: #333; padding: 10px; }
.question { font-weight: bold; font-size: 17px; color: #1a1a2e; }
b { color: #2c3e50; }
ul { margin: 6px 0; padding-left: 20px; }
li { margin: 3px 0; line-height: 1.5; }
hr { border: none; border-top: 1px solid #ddd; margin: 10px 0; }
"""
    )

    anki_deck = genanki.Deck(deck_id, deck_name)
    for card in cards:
        note = genanki.Note(
            model=anki_model,
            fields=[card["question"], card["answer"]]
        )
        anki_deck.add_note(note)

    tmp = tempfile.NamedTemporaryFile(suffix=".apkg", delete=False)
    genanki.Package(anki_deck).write_to_file(tmp.name)
    return tmp.name

async def process_buffered(chat_id: int, update: Update, context: ContextTypes.DEFAULT_TYPE):
    await asyncio.sleep(3)  # Ждём 3 секунды — вдруг ещё сообщения придут

    full_text = "\n".join(message_buffer.pop(chat_id, []))
    pending_tasks.pop(chat_id, None)

    if not full_text.strip():
        return

    deck_name = parse_deck_name(full_text)
    await update.message.reply_text(f"⏳ Генерирую карточки для колоды «{deck_name}»...")

    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{"role": "user", "content": PROMPT + "\n\nТекст пользователя:\n" + full_text}],
            temperature=0.2,
            max_tokens=8000,
        )
        raw = response.choices[0].message.content.strip()
        raw = re.sub(r"```json\s*", "", raw)
        raw = re.sub(r"```\s*", "", raw)

        cards = json.loads(raw)

        if not cards:
            await update.message.reply_text("❌ Не удалось создать карточки")
            return

        apkg_path = create_apkg(cards, deck_name)
        caption = (
            f"✅ Создано {len(cards)} карточек\n"
            f"📚 Колода: {deck_name}\n\n"
            f"Открой файл в Anki → карточки импортируются автоматически"
        )

        with open(apkg_path, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=f"{deck_name.replace('::', '_')}.apkg",
                caption=caption
            )

        os.unlink(apkg_path)

    except json.JSONDecodeError as e:
        await update.message.reply_text(f"❌ Ошибка парсинга JSON: {e}\n\n{raw[:400]}")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Отправь текст в формате:\n\n"
        "Колода: Онкология\n"
        "Подколода: Рак молочной железы\n\n"
        "[твой текст для карточек]\n\n"
        "Я создам .apkg файл — открой его в Anki на телефоне!\n"
        "Можно отправлять текст несколькими сообщениями — подожди 3 секунды и всё склеится."
    )

async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text

    # Добавляем в буфер
    if chat_id not in message_buffer:
        message_buffer[chat_id] = []
    message_buffer[chat_id].append(text)

    # Отменяем предыдущий таймер если есть
    if chat_id in pending_tasks:
        pending_tasks[chat_id].cancel()

    # Запускаем новый таймер
    task = asyncio.create_task(process_buffered(chat_id, update, context))
    pending_tasks[chat_id] = task

app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
print("Бот запущен...")
app.run_polling()
