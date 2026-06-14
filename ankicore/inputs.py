"""Извлечение «частей» ввода из Telegram-сообщения: текст, PDF, фото, голос."""
import asyncio
import io

from . import config


def parse_deck_hint(text: str | None) -> str | None:
    """Вытащить колоду из строк 'Колода:' / 'Подколода:'. None, если не указана."""
    if not text:
        return None
    deck = None
    subdeck = None
    for line in text.splitlines():
        line = line.strip()
        low = line.lower()
        if low.startswith("колода:") or low.startswith("deck:"):
            deck = line.split(":", 1)[1].strip()
        elif low.startswith("подколода:") or low.startswith("subdeck:"):
            subdeck = line.split(":", 1)[1].strip()
    if deck and subdeck:
        return f"{deck}::{subdeck}"
    return deck


async def _download(context, file_id: str) -> bytes:
    f = await context.bot.get_file(file_id)
    return bytes(await f.download_as_bytearray())


def _pdf_to_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        return "\n".join((page.extract_text() or "") for page in reader.pages).strip()
    except Exception:
        return ""


def _office_to_markdown(data: bytes, filename: str) -> str:
    """Word / PowerPoint / Excel → markdown через markitdown."""
    from markitdown import MarkItDown
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    try:
        res = MarkItDown().convert_stream(io.BytesIO(data), file_extension=ext)
        return (res.text_content or "").strip()
    except Exception:
        return ""


_OFFICE_EXT = (".docx", ".pptx", ".xlsx")
_OFFICE_MIME = ("officedocument", "msword", "ms-powerpoint", "ms-excel")


def _transcribe(data: bytes, filename: str) -> str:
    from groq import Groq
    client = Groq(api_key=config.GROQ_API_KEY)
    tr = client.audio.transcriptions.create(
        file=(filename, data),
        model=config.GROQ_WHISPER_MODEL,
    )
    return (tr.text or "").strip()


async def extract(update, context) -> tuple[list[dict], str | None, str | None]:
    """Вернуть (parts, deck_hint, note).

    note — необязательное предупреждение для пользователя (например, нет ключа
    для расшифровки голоса).
    """
    msg = update.message
    parts: list[dict] = []
    note: str | None = None

    text = msg.text or msg.caption
    deck_hint = parse_deck_hint(text)

    # --- Фото ---
    if msg.photo:
        data = await _download(context, msg.photo[-1].file_id)
        parts.append({"type": "image", "mime": "image/jpeg", "data": data})

    # --- Документ (PDF / txt / картинка) ---
    if msg.document:
        doc = msg.document
        mime = (doc.mime_type or "").lower()
        name = (doc.file_name or "").lower()
        data = await _download(context, doc.file_id)
        if mime == "application/pdf" or name.endswith(".pdf"):
            extracted = _pdf_to_text(data)
            if len(extracted) > 200:
                parts.append({"type": "text", "text": extracted})
            else:
                parts.append({"type": "pdf", "data": data})  # вероятно скан → мультимодал
        elif mime.startswith("image/"):
            parts.append({"type": "image", "mime": mime, "data": data})
        elif name.endswith(_OFFICE_EXT) or any(k in mime for k in _OFFICE_MIME):
            md = await asyncio.to_thread(_office_to_markdown, data, name)
            if md:
                parts.append({"type": "text", "text": md})
            else:
                note = "⚠️ Не удалось извлечь текст из документа."
        elif mime.startswith("text/") or name.endswith((".txt", ".md")):
            parts.append({"type": "text", "text": data.decode("utf-8", errors="replace")})
        else:
            note = f"⚠️ Формат {mime or name or 'файла'} не поддерживается — пропущен."

    # --- Голос / аудио ---
    if msg.voice or msg.audio:
        media = msg.voice or msg.audio
        if not config.GROQ_API_KEY:
            note = "⚠️ Для расшифровки голоса нужен GROQ_API_KEY. Сообщение пропущено."
        else:
            data = await _download(context, media.file_id)
            fname = "voice.ogg" if msg.voice else (getattr(media, "file_name", None) or "audio.mp3")
            try:
                transcript = await asyncio.to_thread(_transcribe, data, fname)
                if transcript:
                    parts.append({"type": "text", "text": transcript})
                else:
                    note = "⚠️ Не удалось распознать речь в сообщении."
            except Exception as e:
                note = f"⚠️ Ошибка расшифровки голоса: {e}"

    # --- Текст / подпись ---
    if text:
        parts.append({"type": "text", "text": text})

    return parts, deck_hint, note
