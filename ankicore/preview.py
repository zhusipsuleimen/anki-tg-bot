"""Рендер превью карточек для Telegram и сборка inline-клавиатуры."""
import html
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

PAGE_SIZE = 5
MAX_BACK_CHARS = 600


def html_to_preview(s: str) -> str:
    """Превратить HTML карточки в читабельный plain-text для Telegram."""
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</(p|div|li|tr|pre|h[1-6])>", "\n", s)
    s = re.sub(r"(?i)<li[^>]*>", "• ", s)
    s = re.sub(r"(?i)</t[dh]>", " | ", s)
    s = re.sub(r"(?i)<t[dh][^>]*>", "", s)
    s = re.sub(r"<[^>]+>", "", s)          # остальные теги
    s = html.unescape(s)
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    s = re.sub(r" \| \n", "\n", s)
    return s.strip()


def _truncate(s: str, limit: int) -> str:
    return s if len(s) <= limit else s[:limit].rstrip() + " …"


def render_preview(cards: list[dict], deck: str, provider: str, page: int) -> str:
    pages = max(1, (len(cards) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, pages - 1))
    start = page * PAGE_SIZE
    chunk = cards[start:start + PAGE_SIZE]

    header = (
        f"📋 Готово карточек: {len(cards)}\n"
        f"📚 Колода: {deck}\n"
        f"🤖 Модель: {provider}\n"
        f"───────────────"
    )
    blocks = []
    for i, c in enumerate(chunk, start=start + 1):
        back = _truncate(html_to_preview(c["back"]), MAX_BACK_CHARS)
        blocks.append(f"{i}. {html_to_preview(c['front'])}\n{back}")

    footer = f"───────────────\nСтраница {page + 1}/{pages}" if pages > 1 else ""
    return "\n\n".join([header] + blocks + ([footer] if footer else []))


def build_keyboard(cards: list[dict], page: int) -> InlineKeyboardMarkup:
    pages = max(1, (len(cards) + PAGE_SIZE - 1) // PAGE_SIZE)
    rows = []
    if pages > 1:
        rows.append([
            InlineKeyboardButton("⬅️", callback_data="pg:prev"),
            InlineKeyboardButton(f"{page + 1}/{pages}", callback_data="pg:noop"),
            InlineKeyboardButton("➡️", callback_data="pg:next"),
        ])
    rows.append([InlineKeyboardButton(f"✅ Добавить все ({len(cards)})", callback_data="do:add")])
    rows.append([
        InlineKeyboardButton("✏️ Переделать", callback_data="do:redo"),
        InlineKeyboardButton("❌ Отмена", callback_data="do:cancel"),
    ])
    return InlineKeyboardMarkup(rows)
