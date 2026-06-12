"""Облачный бот: присылает готовый .apkg файл в чат.

Работает 24/7 где угодно (Railway/Render/VPS) — Anki не нужен. Пользователь
открывает .apkg и импортирует карточки (в т.ч. на телефоне).
    .venv/bin/python bot_cloud.py
"""
import asyncio
import os

from ankicore import config
from ankicore.apkg import create_apkg
from ankicore.flow import Sink, build_app


class ApkgSink(Sink):
    label = "файл .apkg в чат"

    async def deliver(self, cards, deck, query, context) -> str:
        path = await asyncio.to_thread(create_apkg, cards, deck)
        try:
            with open(path, "rb") as f:
                await query.message.reply_document(
                    document=f,
                    filename=f"{deck.replace('::', '_')}.apkg",
                    caption=(
                        f"✅ {len(cards)} карточек · 📚 {deck}\n"
                        f"Открой файл в Anki → импорт → синхронизация."
                    ),
                )
        finally:
            os.unlink(path)
        return f"✅ Готово: {len(cards)} карточек для «{deck}». Файл .apkg отправлен ⬆️"

    async def readiness(self) -> str | None:
        return "📎 Карточки придут файлом .apkg — открой его в Anki."


def main():
    app = build_app(config.TELEGRAM_TOKEN_CLOUD, ApkgSink())
    print("Облачный бот запущен (.apkg)…")
    app.run_polling()


if __name__ == "__main__":
    main()
