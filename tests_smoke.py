"""Офлайн-проверка ядра без сети и без Telegram-токена."""
import asyncio
import os

# 1) Импорт всех модулей (flow тянет telegram, config — dotenv)
from ankicore import config, prompt, llm, apkg, ankiconnect, preview, inputs, flow
import bot_local, bot_cloud  # noqa: F401  (импорт без запуска main)
print("[1] импорты ОК")

# 2) Парсинг JSON из «грязного» ответа модели
raw = """```json
[
  {"front": "Тест (2)", "back": "<ul><li><b>А</b> — раз</li><li>Б</li></ul>", "deck": "X::Y"},
  {"front": "Голый факт", "back": "Опухоль <b>до 2 см</b>.<br>Мтс нет."}
]
```"""
data = llm._extract_json_array(raw)
cards = llm._normalize_cards(data, deck_hint=None)
assert len(cards) == 2, cards
assert cards[0]["deck"] == "X::Y"
assert cards[1]["deck"] == "Anki"  # дефолт, если в карточке нет deck
cards_over = llm._normalize_cards(data, deck_hint="Онко::РМЖ")
assert all(c["deck"] == "Онко::РМЖ" for c in cards_over)
print("[2] парсинг JSON и override колоды ОК")

# 3) html_to_preview
pv = preview.html_to_preview("<ul style=\"list-style-type: '→ '\"><li><b>Термин</b> — пояснение</li></ul>")
assert "Термин" in pv and "<" not in pv, repr(pv)
pv2 = preview.html_to_preview("Опухоль <b>до 2 см</b>.<br>Мтс нет.")
assert pv2 == "Опухоль до 2 см.\nМтс нет.", repr(pv2)
print("[3] html_to_preview ОК")

# 4) render_preview + клавиатура (пагинация)
many = [{"front": f"Q{i}", "back": f"A{i}", "deck": "D"} for i in range(12)]
text = preview.render_preview(many, "D", "Claude (x)", page=0)
assert "Готово карточек: 12" in text
kb = preview.build_keyboard(many, 0)
assert len(kb.inline_keyboard) == 3  # пагинация + добавить + переделать/отмена
print("[4] превью и клавиатура ОК")

# 5) parse_deck_hint
assert inputs.parse_deck_hint("Колода: Онкология\nПодколода: РМЖ\nтекст") == "Онкология::РМЖ"
assert inputs.parse_deck_hint("просто текст") is None
print("[5] parse_deck_hint ОК")

# 6) Сборка .apkg
path = apkg.create_apkg(cards, "Тест::Колода")
assert os.path.exists(path) and os.path.getsize(path) > 1000, path
os.unlink(path)
print("[6] сборка .apkg ОК (файл валиден)")

# 7) AnkiConnect доступность (живая проверка localhost:8765)
available = asyncio.run(ankiconnect.is_available())
print(f"[7] AnkiConnect доступен: {available}")

# 8) Конфиг: какие провайдеры/ключи видны
print(f"    провайдеры с ключами: {config.available_providers() or 'нет'}")
print(f"    дефолтный провайдер: {config.DEFAULT_PROVIDER}")
print(f"    Claude модель: {config.ANTHROPIC_MODEL}; Gemini модель: {config.GEMINI_MODEL}")
print("\nВСЕ ОФЛАЙН-ПРОВЕРКИ ПРОШЛИ ✅")
