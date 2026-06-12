"""Общий сценарий Telegram-бота: ввод → буфер → генерация → превью → кнопки.

Параметризуется «раковиной» (Sink): куда уходят подтверждённые карточки —
напрямую в Anki (AnkiConnect) или в виде .apkg файла.
"""
import asyncio

from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters,
)

from . import config
from .inputs import extract
from .llm import generate_cards, LLMError
from .preview import render_preview, build_keyboard, PAGE_SIZE

DEBOUNCE_SECONDS = 2.5
TG_LIMIT = 4000


class Sink:
    """Интерфейс доставки карточек. Переопредели deliver().

    deliver() получает query/context, чтобы при необходимости отправлять
    дополнительные сообщения или файлы. Возвращает текст, которым будет
    заменено превью.
    """
    label = "—"

    async def deliver(self, cards: list[dict], deck: str, query, context) -> str:
        raise NotImplementedError

    async def readiness(self) -> str | None:
        return None


def provider_label(provider: str) -> str:
    provider = config.resolve_provider(provider)
    if provider == "gemini":
        return f"Gemini ({config.GEMINI_MODEL})"
    return f"Claude ({config.ANTHROPIC_MODEL})"


def _current_provider(context) -> str:
    return context.chat_data.get("provider") or config.DEFAULT_PROVIDER


# --- Команды --------------------------------------------------------------

def _make_start(sink: Sink):
    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        provider = config.resolve_provider(_current_provider(context))
        avail = config.available_providers()
        lines = [
            "👋 Привет! Я делаю Anki-карточки из твоих материалов.",
            "",
            "Пришли мне:",
            "• 📝 текст (можно несколькими сообщениями подряд)",
            "• 📄 PDF",
            "• 🖼 фото страницы / скриншот",
            "• 🎤 голосовое сообщение",
            "",
            "Чтобы задать колоду, добавь в начало текста:",
            "Колода: Онкология",
            "Подколода: РМЖ",
            "",
            f"📦 Доставка: {sink.label}",
            f"🤖 Модель: {provider_label(provider)}",
        ]
        if len(avail) > 1:
            lines.append("Переключить модель: /claude или /gemini")
        if not avail:
            lines.append("\n⚠️ Не задан ни один ключ LLM (ANTHROPIC_API_KEY или GEMINI_API_KEY).")
        ready = await sink.readiness()
        if ready:
            lines.append("\n" + ready)
        await update.message.reply_text("\n".join(lines))
    return start


def _make_switch(provider_name: str):
    async def switch(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if provider_name == "claude" and not config.ANTHROPIC_API_KEY:
            await update.message.reply_text("⚠️ Не задан ANTHROPIC_API_KEY.")
            return
        if provider_name == "gemini" and not config.GEMINI_API_KEY:
            await update.message.reply_text("⚠️ Не задан GEMINI_API_KEY.")
            return
        context.chat_data["provider"] = provider_name
        await update.message.reply_text(f"🤖 Модель переключена: {provider_label(provider_name)}")
    return switch


# --- Приём сообщений (с дебаунсом) ---------------------------------------

def _make_on_message(sink: Sink):
    async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
        parts, deck_hint, note = await extract(update, context)
        if note:
            await update.message.reply_text(note)
        if not parts:
            return

        context.chat_data.setdefault("buf_parts", []).extend(parts)
        if deck_hint:
            context.chat_data["buf_deck"] = deck_hint

        old = context.chat_data.get("debounce_task")
        if old and not old.done():
            old.cancel()
        context.chat_data["debounce_task"] = asyncio.create_task(
            _debounced_generate(update, context, sink)
        )
    return on_message


async def _debounced_generate(update, context, sink: Sink):
    try:
        await asyncio.sleep(DEBOUNCE_SECONDS)
    except asyncio.CancelledError:
        return

    parts = context.chat_data.pop("buf_parts", [])
    deck_hint = context.chat_data.pop("buf_deck", None)
    context.chat_data.pop("debounce_task", None)
    if not parts:
        return

    await _generate_and_preview(update, context, sink, parts, deck_hint)


async def _generate_and_preview(update, context, sink: Sink, parts, deck_hint):
    provider = config.resolve_provider(_current_provider(context))
    status = await update.effective_chat.send_message("⏳ Генерирую карточки…")
    await update.effective_chat.send_action(ChatAction.TYPING)

    try:
        cards = await asyncio.to_thread(generate_cards, parts, provider, deck_hint)
    except LLMError as e:
        await status.edit_text(f"❌ {e}")
        return
    except Exception as e:
        await status.edit_text(f"❌ Ошибка генерации: {e}")
        return

    deck = deck_hint or cards[0]["deck"]
    context.chat_data["pending"] = {
        "cards": cards, "deck": deck, "parts": parts,
        "provider": provider, "page": 0, "msg_id": status.message_id,
    }
    text = render_preview(cards, deck, provider_label(provider), 0)
    await status.edit_text(text[:TG_LIMIT], reply_markup=build_keyboard(cards, 0))


# --- Кнопки ---------------------------------------------------------------

def _make_on_callback(sink: Sink):
    async def on_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        data = query.data
        pending = context.chat_data.get("pending")

        if not pending:
            await query.edit_message_text("⌛️ Сессия истекла. Пришли материал заново.")
            return

        cards = pending["cards"]
        deck = pending["deck"]
        provider = pending["provider"]

        if data == "pg:noop":
            return

        if data in ("pg:prev", "pg:next"):
            pages = max(1, (len(cards) + PAGE_SIZE - 1) // PAGE_SIZE)
            page = pending["page"] + (1 if data == "pg:next" else -1)
            page = max(0, min(page, pages - 1))
            pending["page"] = page
            text = render_preview(cards, deck, provider_label(provider), page)
            await query.edit_message_text(text[:TG_LIMIT], reply_markup=build_keyboard(cards, page))
            return

        if data == "do:cancel":
            context.chat_data.pop("pending", None)
            await query.edit_message_text("❌ Отменено.")
            return

        if data == "do:redo":
            await query.edit_message_text("🔄 Переделываю…")
            try:
                new_cards = await asyncio.to_thread(generate_cards, pending["parts"], provider, deck)
            except Exception as e:
                await query.edit_message_text(f"❌ Ошибка: {e}")
                return
            pending["cards"] = new_cards
            pending["page"] = 0
            text = render_preview(new_cards, deck, provider_label(provider), 0)
            await query.edit_message_text(text[:TG_LIMIT], reply_markup=build_keyboard(new_cards, 0))
            return

        if data == "do:add":
            await query.edit_message_text(f"⏳ Добавляю {len(cards)} карточек в «{deck}»…")
            try:
                result = await sink.deliver(cards, deck, query, context)
            except Exception as e:
                await query.edit_message_text(f"❌ Ошибка доставки: {e}")
                return
            context.chat_data.pop("pending", None)
            await query.edit_message_text(result)
            return
    return on_callback


# --- Сборка приложения ----------------------------------------------------

def build_app(token: str, sink: Sink):
    if not token:
        raise SystemExit(
            "Не задан Telegram-токен. Укажи TELEGRAM_TOKEN (или TELEGRAM_TOKEN_LOCAL/"
            "TELEGRAM_TOKEN_CLOUD) в .env."
        )
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler(["start", "help"], _make_start(sink)))
    app.add_handler(CommandHandler("claude", _make_switch("claude")))
    app.add_handler(CommandHandler("gemini", _make_switch("gemini")))
    content = (
        (filters.TEXT & ~filters.COMMAND)
        | filters.PHOTO | filters.VOICE | filters.AUDIO | filters.Document.ALL
    )
    app.add_handler(MessageHandler(content, _make_on_message(sink)))
    app.add_handler(CallbackQueryHandler(_make_on_callback(sink)))
    return app
