"""Облачный бот + мини-приложение (Telegram Mini App).

В одном процессе крутятся:
  • long-polling Telegram (чат: текст/PDF/фото/голос → превью → .apkg);
  • веб-сервер FastAPI, который отдаёт мини-приложение и его API.

Работает 24/7 где угодно (Railway/Render/VPS) — Anki не нужен. Карточки
приходят файлом .apkg, его открывают в Anki (в т.ч. на телефоне).
    .venv/bin/python bot_cloud.py
"""
import asyncio
import os

from telegram import MenuButtonWebApp, WebAppInfo

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


async def _set_menu_button(app):
    """Поставить кнопку-меню «Открыть мини-приложение» рядом с полем ввода."""
    if not config.WEBAPP_URL:
        print("WEBAPP_URL не задан — кнопка мини-приложения не ставится "
              "(чат работает как обычно).")
        return
    try:
        await app.bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="🃏 Карточки",
                web_app=WebAppInfo(url=config.WEBAPP_URL),
            )
        )
        print(f"Кнопка мини-приложения настроена: {config.WEBAPP_URL}")
    except Exception as e:
        print(f"Не удалось поставить menu button: {e}")


async def _serve_web(app):
    """Запустить FastAPI-сервер мини-приложения (блокирует до остановки)."""
    import uvicorn

    from ankicore.webapp import create_web_app

    web = create_web_app(bot=app.bot, token=config.TELEGRAM_TOKEN_CLOUD)
    cfg = uvicorn.Config(
        web, host="0.0.0.0", port=config.PORT, log_level="info", lifespan="off",
    )
    server = uvicorn.Server(cfg)
    await server.serve()


async def main_async():
    app = build_app(config.TELEGRAM_TOKEN_CLOUD, ApkgSink())
    async with app:
        await app.start()
        await app.updater.start_polling()
        await _set_menu_button(app)
        print(f"Облачный бот + мини-приложение запущены (порт {config.PORT})…")
        try:
            await _serve_web(app)          # держит процесс живым
        finally:
            await app.updater.stop()
            await app.stop()


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
