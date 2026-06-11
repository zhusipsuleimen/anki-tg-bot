import os
import json
import re
import tempfile
import threading
import time
import genanki
import random
from flask import Flask, request
import google.generativeai as genai
from telegram import Update
from telegram.ext import ApplicationBuilder, MessageHandler, CommandHandler, filters, ContextTypes

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", "").rstrip("/")

genai.configure(api_key=GEMINI_API_KEY)
gemini = genai.GenerativeModel("gemini-2.0-flash")

flask_app = Flask(__name__)

PROMPT = """Ты эксперт по созданию Anki карточек для медицинских студентов.

Пользователь пришлёт текст с указанием колоды и подколоды.

ФОРМАТ ОТВЕТА — верни строго валидный JSON массив карточек:
[
  {
    "question": "Вопрос — широкая тема, объединяющая группу фактов",
    "answer": "<b>Категория:</b><ul><li><b>Термин</b> — пояснение, цифры</li></ul>",
    "deck": "Онкология::Рак молочной железы"
  }
]

ПРАВИЛА:
1. Каждая карточка охватывает логически связанную группу фактов — не один факт
2. Все цифры, проценты, механизмы должны быть в ответе
3. Охвати ВЕСЬ текст без исключений
4. Используй HTML теги: <b>жирный</b>, <ul><li>список</li></ul>
5. Поле "deck" — точное название колоды как указал пользователь
6. Верни ТОЛЬКО JSON, никакого другого текста

Запрос пользователя:
"""

def parse_deck_name(text: str) -> str:
    lines = text.strip().split('\n')
    deck = "Anki"
    subdeck = None
    for line in lines:
        if line.lower().startswith("колода:"):
            deck = line.split(":", 1)[1].strip()
        elif line.lower().startswith("подколода:"):
            subdeck = line.split(":", 1)[1].strip()
    if subdeck:
        return f"{deck}::{subdeck}"
    return deck

def create_apkg(cards: list, deck_name: str) -> str:
    deck_id = random.randrange(1 << 30, 1 << 31)
    model_id = random.randrange(1 << 30, 1 << 31)

    anki_model = genanki.Model(
        model_id,
        "Basic HTML",
        fields=[{"name": "Question"}, {"name": "Answer"}],
        templates=[{
            "name": "Card 1",
            "qfmt": "{{Question}}",
            "afmt": "{{FrontSide}}<hr id=answer>{{Answer}}",
        }],
        css="""
.card { font-family: Arial, sans-serif; font-size: 16px; text-align: left; }
b { color: #2c3e50; }
ul { margin: 4px 0; padding-left: 20px; }
li { margin: 2px 0; }
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

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Отправь текст в формате:\n\n"
        "Колода: Онкология\n"
        "Подколода: Рак молочной железы\n\n"
        "[твой текст для карточек]\n\n"
        "Я создам .apkg файл — открой его в Anki на телефоне!"
    )

async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_input = update.message.text
    await update.message.reply_text("⏳ Генерирую карточки...")

    deck_name = parse_deck_name(user_input)

    try:
        response = gemini.generate_content(PROMPT + user_input)
        raw = response.text.strip()
        raw = re.sub(r"```json\s*", "", raw)
        raw = re.sub(r"```\s*", "", raw)

        cards = json.loads(raw)

        if not cards:
            await update.message.reply_text("❌ Не удалось создать карточки")
            return

        apkg_path = create_apkg(cards, deck_name)
        caption = f"✅ Создано {len(cards)} карточек\nКолода: {deck_name}\n\nОткрой файл в Anki → импортируй → синхронизируй"

        with open(apkg_path, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=f"{deck_name.replace('::', '_')}.apkg",
                caption=caption
            )

        os.unlink(apkg_path)

    except json.JSONDecodeError as e:
        await update.message.reply_text(f"❌ Ошибка парсинга JSON: {e}\n\nОтвет Gemini:\n{raw[:500]}")
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")

tg_app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
tg_app.add_handler(CommandHandler("start", start))
tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle))

@flask_app.post(f"/webhook/{TELEGRAM_TOKEN}")
async def webhook():
    data = request.get_json()
    update = Update.de_json(data, tg_app.bot)
    await tg_app.initialize()
    await tg_app.process_update(update)
    return "ok"

@flask_app.get("/")
def health():
    return "ok"

def register_webhook():
    # Ждём пока Flask поднимется, потом регистрируем webhook
    time.sleep(5)
    import asyncio
    async def _set():
        await tg_app.initialize()
        url = f"{WEBHOOK_URL}/webhook/{TELEGRAM_TOKEN}"
        await tg_app.bot.set_webhook(url)
        print(f"Webhook установлен: {url}")
    asyncio.run(_set())

if __name__ == "__main__":
    if WEBHOOK_URL:
        t = threading.Thread(target=register_webhook, daemon=True)
        t.start()
    port = int(os.environ.get("PORT", 5000))
    flask_app.run(host="0.0.0.0", port=port)
