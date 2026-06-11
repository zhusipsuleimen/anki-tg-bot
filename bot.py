import os
import json
import re
import tempfile
import asyncio
import genanki
import random
import httpx
from groq import Groq
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GROQ_API_KEY = os.environ["GROQ_API_KEY"]
# Если задан ANKI_NGROK_URL — карточки создаются прямо в Anki через MCP HTTP сервер
ANKI_NGROK_URL = os.environ.get("ANKI_NGROK_URL", "").rstrip("/")

groq_client = Groq(api_key=GROQ_API_KEY)

# Буфер для склейки сообщений (chat_id -> список сообщений)
message_buffer: dict[int, list[str]] = {}
pending_tasks: dict[int, asyncio.Task] = {}

PROMPT = """Ты эксперт по созданию Anki карточек для медицинских студентов.

Пользователь пришлёт текст. В начале текста могут быть указаны колода и подколода:
Колода: [название]
Подколода: [название]

ФОРМАТ КАРТОЧЕК:

Вопрос — широкая тема, объединяющая логически связанную группу фактов. Примеры:
"Муцинозная цистаденома яичника — характеристика, размеры, риск"
"RMI — формула, компоненты, интерпретация"
"Классификация TNM рака молочной железы — критерии T, N, M"

Ответ — структурированный HTML:
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
1. Каждая карточка = логически связанная группа фактов (НЕ один факт!)
2. Охвати АБСОЛЮТНО ВЕСЬ текст — ни один факт, цифра, термин не пропущен
3. Все цифры, проценты, критерии, стадии, механизмы — обязательно в ответе
4. Используй ТОЛЬКО HTML: <b>, <ul>, <li>
5. Верни ТОЛЬКО валидный JSON массив, никакого другого текста:

[
  {
    "question": "Тема карточки",
    "answer": "<b>Раздел:</b><ul><li><b>Термин</b> — пояснение</li></ul>",
    "deck": "Онкология::Рак молочной железы"
  }
]

Поле "deck" — точное название колоды::подколоды как указал пользователь."""

CARD_CSS = """
.card {
  font-family: Arial, sans-serif;
  font-size: 16px;
  text-align: left;
  color: #333;
  padding: 12px;
  max-width: 600px;
  margin: 0 auto;
}
.question {
  font-weight: bold;
  font-size: 18px;
  color: #1a1a2e;
  margin-bottom: 8px;
}
b { color: #2c3e50; }
ul { margin: 6px 0; padding-left: 20px; }
li { margin: 4px 0; line-height: 1.6; }
hr { border: none; border-top: 1px solid #ddd; margin: 10px 0; }
"""

def parse_deck_name(text: str) -> str:
    lines = text.strip().split('\n')
    deck = "Anki"
    subdeck = None
    for line in lines:
        stripped = line.strip()
        if stripped.lower().startswith("колода:"):
            deck = stripped.split(":", 1)[1].strip()
        elif stripped.lower().startswith("подколода:"):
            subdeck = stripped.split(":", 1)[1].strip()
    return f"{deck}::{subdeck}" if subdeck else deck

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
        css=CARD_CSS
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

async def send_to_anki_direct(cards: list) -> tuple[bool, str]:
    """Отправляет карточки напрямую в Anki через MCP HTTP сервер (ankimcp --ngrok)"""
    notes = []
    for card in cards:
        deck = card.get("deck", "Anki")
        notes.append({
            "deckName": deck,
            "modelName": "Basic",
            "fields": {
                "Front": card["question"],
                "Back": card["answer"]
            },
            "options": {"allowDuplicate": False},
            "tags": []
        })

    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": "addNotes",
            "arguments": {"notes": notes}
        },
        "id": 1
    }

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(ANKI_NGROK_URL, json=payload)
        resp.raise_for_status()
        result = resp.json()
        if "error" in result:
            return False, str(result["error"])
        return True, ""

async def call_groq(full_text: str) -> list:
    response = groq_client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": PROMPT + "\n\nТекст пользователя:\n" + full_text}],
        temperature=0.2,
        max_tokens=8000,
    )
    raw = response.choices[0].message.content.strip()
    raw = re.sub(r"```json\s*", "", raw)
    raw = re.sub(r"```\s*", "", raw)
    return json.loads(raw)

async def process_buffered(chat_id: int, update: Update, context: ContextTypes.DEFAULT_TYPE):
    await asyncio.sleep(3)

    full_text = "\n".join(message_buffer.pop(chat_id, []))
    pending_tasks.pop(chat_id, None)

    if not full_text.strip():
        return

    deck_name = parse_deck_name(full_text)
    await update.message.reply_text(f"⏳ Генерирую карточки для «{deck_name}»...")

    try:
        cards = await call_groq(full_text)

        if not cards:
            await update.message.reply_text("❌ Не удалось создать карточки")
            return

        # Режим 1: Mac включён и ankimcp --ngrok запущен
        if ANKI_NGROK_URL:
            ok, err = await send_to_anki_direct(cards)
            if ok:
                await update.message.reply_text(
                    f"✅ Создано {len(cards)} карточек прямо в Anki!\n"
                    f"📚 Колода: {deck_name}\n\n"
                    f"Синхронизируй Anki чтобы карточки появились на телефоне."
                )
                return
            else:
                await update.message.reply_text(f"⚠️ Anki недоступен ({err}), отправляю .apkg файл...")

        # Режим 2: отправляем .apkg файл
        apkg_path = create_apkg(cards, deck_name)
        caption = (
            f"✅ Создано {len(cards)} карточек\n"
            f"📚 Колода: {deck_name}\n\n"
            f"Открой файл в Anki на телефоне → карточки импортируются автоматически → синхронизируй"
        )

        with open(apkg_path, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=f"{deck_name.replace('::', '_')}.apkg",
                caption=caption
            )
        os.unlink(apkg_path)

    except json.JSONDecodeError as e:
        await update.message.reply_text(f"❌ Ошибка парсинга JSON: {e}")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    mode = "прямо в Anki (Mac включён)" if ANKI_NGROK_URL else ".apkg файл для импорта в Anki"
    await update.message.reply_text(
        f"Привет! Режим: {mode}\n\n"
        "Отправь текст в формате:\n\n"
        "Колода: Онкология\n"
        "Подколода: Рак молочной железы\n\n"
        "[твой текст для карточек]\n\n"
        "Можно отправлять несколькими сообщениями — подожди 3 секунды и всё склеится."
    )

async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = update.message.text

    if chat_id not in message_buffer:
        message_buffer[chat_id] = []
    message_buffer[chat_id].append(text)

    if chat_id in pending_tasks:
        pending_tasks[chat_id].cancel()

    task = asyncio.create_task(process_buffered(chat_id, update, context))
    pending_tasks[chat_id] = task

app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))
print("Бот запущен...")
app.run_polling()
