# anki-tg-bot

Telegram-бот, который превращает текст, PDF, фото и голосовые в Anki-карточки
(в медицинском стиле). Перед добавлением показывает превью с кнопками.

Есть **два бота** на общем ядре:

| Бот | Файл | Как карточки попадают в Anki | Когда работает |
|-----|------|------------------------------|----------------|
| **Локальный** | `bot_local.py` | напрямую в Anki через AnkiConnect | пока Mac включён и Anki открыт |
| **Облачный** | `bot_cloud.py` | присылает `.apkg` файлом в чат | 24/7, Anki не нужен, работает и с телефона |

## Что умеет

- **Входы:** 📝 текст · 📄 PDF · 🖼 фото/скриншот · 🎤 голосовое
- **Превью + кнопки:** «✅ Добавить все», «✏️ Переделать», «❌ Отмена», листание страниц
- **Два LLM:** Claude и Gemini, переключаются в чате командами `/claude` и `/gemini`
- **Колоды:** задаются в начале текста (`Колода:` / `Подколода:`)

## Установка

```bash
git clone https://github.com/zhusipsuleimen/anki-tg-bot.git
cd anki-tg-bot
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env      # затем впиши ключи в .env
```

### Ключи (в `.env`)

| Переменная | Где взять | Обязательность |
|-----------|-----------|----------------|
| `TELEGRAM_TOKEN_LOCAL` / `TELEGRAM_TOKEN_CLOUD` | [@BotFather](https://t.me/BotFather) | да (для нужного бота) |
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) | хотя бы один из LLM |
| `GEMINI_API_KEY` | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) | хотя бы один из LLM |
| `GROQ_API_KEY` | [console.groq.com/keys](https://console.groq.com/keys) | только для голосовых |

> **Два бота одновременно = два разных токена.** Telegram разрешает один
> long-poll на токен, поэтому создай в @BotFather двух ботов и впиши их токены
> в `TELEGRAM_TOKEN_LOCAL` и `TELEGRAM_TOKEN_CLOUD`. Если запускаешь только один
> бот — хватит общего `TELEGRAM_TOKEN`.

## Запуск

### Локальный бот (карточки сразу в Anki)

1. Открой Anki, установи аддон **AnkiConnect** (код `2055492159`), перезапусти Anki.
2. Запусти:
   ```bash
   .venv/bin/python bot_local.py
   ```
3. Напиши боту `/start`. Если Anki на связи — увидишь 🟢.

### Облачный бот (карточки файлом)

Локально:
```bash
.venv/bin/python bot_cloud.py
```

Деплой 24/7 (Railway / Render / любой VPS):
- `Procfile` уже настроен: `worker: python bot_cloud.py`
- Python пинится через `.python-version` (3.12)
- Задай переменные окружения на платформе: `TELEGRAM_TOKEN_CLOUD`,
  `ANTHROPIC_API_KEY` (и/или `GEMINI_API_KEY`), при желании `GROQ_API_KEY`.

## Как пользоваться

Отправь боту материал. Чтобы указать колоду — добавь в начало:

```
Колода: Онкология
Подколода: РМЖ

[текст / или просто пришли фото, PDF, голосовое]
```

Бот соберёт несколько сообщений подряд (пауза ~2.5 с), сгенерирует карточки,
покажет превью и кнопки. Жмёшь «✅ Добавить все» — карточки уходят в Anki
(локальный бот) или приходят `.apkg` файлом (облачный бот).

### Команды

- `/start`, `/help` — приветствие и статус
- `/claude` — генерировать через Claude
- `/gemini` — генерировать через Gemini

## Проверка ядра без сети

```bash
.venv/bin/python tests_smoke.py
```

## Структура

```
ankicore/
  config.py        переменные окружения
  prompt.py        промпт + стиль карточек (общий для .apkg и AnkiConnect)
  llm.py           генерация: Claude / Gemini (мультимодал)
  inputs.py        текст / PDF / фото / голос → «части» ввода
  apkg.py          сборка .apkg (облачный бот)
  ankiconnect.py   добавление в Anki (локальный бот)
  preview.py       превью карточек + кнопки
  flow.py          общий сценарий диалога
bot_local.py       вход: AnkiConnect
bot_cloud.py       вход: .apkg
```
