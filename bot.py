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

PROMPT = """Ты создаёшь Anki карточки для медицинских студентов. Воспроизводи ТОЧНО стиль примеров ниже.

═══════════════════════════════
СТИЛЬ ВОПРОСА:
Краткая тема + уточнение + (N) если уместно.
Примеры:
• "КРР: основные причины актуальности проблемы (3)"
• "4 группы факторов риска РМЖ"
• "Боли при раке правой vs левой половины ТК"
• "Степени злокачественности РМЖ по сумме баллов (Scarff–Bloom–Richardson)"
• "Рак ободочной кишки: стадии (отечественная классификация I–IV)"
• "Сравнение симптомов: рак правой vs левой половины ТК"

═══════════════════════════════
СТИЛЬ ОТВЕТА — выбирай по типу информации:

1. СПИСОК с кастомными стрелками — для перечислений, факторов, путей:
<ul style="list-style-type: '→ '"><li><b>Термин</b> — пояснение с цифрами</li></ul>

2. СПИСОК с ромбом — для категорий/групп:
<ul style="list-style-type: '◆ '"><li>Категория (подробности)</li></ul>

3. PRE-БЛОК — для статистики, цифр, распределений:
<pre>Новые случаи: 68 064
Умерло:       40 208 (54.9% — жен.)</pre>

4. ТАБЛИЦА — ТОЛЬКО для сравнений двух объектов:
<table border="1" cellpadding="4">
<tr><th>Симптом</th><th>Правая</th><th>Левая</th></tr>
<tr><td>Боли</td><td>91.3%</td><td>82%</td></tr>
</table>

5. PRE + UL — для разделённых групп:
<pre>Экзофитный:</pre><ul style="list-style-type: circle;"><li>Узловая</li></ul>
<pre>Эндофитный:</pre><ul style="list-style-type: circle;"><li>Язвенная</li></ul>

6. НУМЕРОВАННЫЙ — для клинических форм, стадий:
<ol><li>Токсико-анемическая</li><li>Диспептическая</li></ol>

7. ПУЛЯ + BR — для коротких простых списков:
• Синдром Линча<br>• Семейный аденоматоз<br>• MutYH-ассоциированный полипоз

8. ТЕКСТ + B — для определений, механизмов, одиночных фактов:
Опухоль <b>до 2 см</b>, без прорастания в жировую клетчатку.<br>Метастазы <b>отсутствуют</b>.

═══════════════════════════════
ПРАВИЛА:
- Каждый раздел, подраздел, вид/форма/линия терапии → отдельная карточка
- Все цифры (дозы мг/кг/сут, %, сроки дней, pH) — обязательно в ответе
- НЕ используй <b>Заголовок:</b> перед каждым блоком — только по смыслу
- Для большого текста 15–25 карточек норма
- Поле "deck" = колода::подколода (если не указано — "Anki")

Верни ТОЛЬКО валидный JSON массив, без объяснений:
[
  {
    "front": "Краткий вопрос (N)",
    "back": "HTML ответ в нужном стиле",
    "deck": "Онкология::РМЖ"
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
        response = await asyncio.to_thread(
            groq_client.chat.completions.create,
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
