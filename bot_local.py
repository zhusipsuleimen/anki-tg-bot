"""Локальный бот: добавляет карточки напрямую в Anki через AnkiConnect.

Запускать на том же Mac, где открыт Anki (с аддоном AnkiConnect).
    .venv/bin/python bot_local.py
"""
from ankicore import config, ankiconnect
from ankicore.flow import Sink, build_app


class AnkiConnectSink(Sink):
    label = "напрямую в Anki (AnkiConnect)"

    async def deliver(self, cards, deck, query, context) -> str:
        added, skipped = await ankiconnect.add_cards(cards, deck)
        msg = f"✅ Добавлено {added} карточек в «{deck}»."
        if skipped:
            msg += f"\n⏭ Пропущено дублей: {skipped}."
        msg += "\n\nОни уже в Anki — синхронизируй, если нужно на других устройствах."
        return msg

    async def readiness(self) -> str | None:
        if await ankiconnect.is_available():
            return "🟢 Anki на связи — карточки будут добавляться сразу."
        return (
            "🔴 Anki не отвечает. Открой Anki с аддоном AnkiConnect — "
            f"бот ждёт его на {config.ANKICONNECT_URL}."
        )


def main():
    app = build_app(config.TELEGRAM_TOKEN_LOCAL, AnkiConnectSink())
    print("Локальный бот запущен (AnkiConnect)…")
    app.run_polling()


if __name__ == "__main__":
    main()
