"""Слой LLM: генерация карточек через Claude или Gemini.

Оба провайдера принимают мультимодальный вход (текст + изображения + PDF) одним
вызовом. Внутренний формат "частей" (parts):
    {"type": "text",  "text": str}
    {"type": "image", "mime": str, "data": bytes}
    {"type": "pdf",   "data": bytes}
"""
import base64
import json
import re

from . import config
from .prompt import PROMPT


class LLMError(Exception):
    pass


# --- Парсинг ответа -------------------------------------------------------

def _extract_json_array(raw: str) -> list[dict]:
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()
    # strict=False разрешает «сырые» переводы строк/табы внутри строк JSON —
    # Claude часто вставляет их в <pre>-блоки, и строгий парсер бы падал
    # ("Invalid control character").
    try:
        data = json.loads(text, strict=False)
    except json.JSONDecodeError:
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end == -1 or end <= start:
            raise LLMError(f"Не удалось разобрать JSON из ответа модели:\n{raw[:400]}")
        data = json.loads(text[start:end + 1], strict=False)

    if isinstance(data, dict):
        # Иногда модель оборачивает в {"cards": [...]}
        for key in ("cards", "карточки", "items", "data"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            data = [data]
    if not isinstance(data, list):
        raise LLMError("Ответ модели не является списком карточек")
    return data


def _normalize_cards(data: list, deck_hint: str | None) -> list[dict]:
    cards = []
    for item in data:
        if not isinstance(item, dict):
            continue
        front = str(item.get("front") or item.get("вопрос") or "").strip()
        back = str(item.get("back") or item.get("ответ") or "").strip()
        if not front or not back:
            continue
        deck = deck_hint or str(item.get("deck") or "Anki").strip() or "Anki"
        cards.append({"front": front, "back": back, "deck": deck})
    return cards


# --- Claude ---------------------------------------------------------------

def _claude_generate(parts: list[dict]) -> str:
    import anthropic

    if not config.ANTHROPIC_API_KEY:
        raise LLMError("Не задан ANTHROPIC_API_KEY")

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    content = []
    for p in parts:
        if p["type"] == "text":
            content.append({"type": "text", "text": p["text"]})
        elif p["type"] == "image":
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": p["mime"],
                    "data": base64.standard_b64encode(p["data"]).decode("ascii"),
                },
            })
        elif p["type"] == "pdf":
            content.append({
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": base64.standard_b64encode(p["data"]).decode("ascii"),
                },
            })
    if not content:
        raise LLMError("Пустой ввод")

    resp = client.messages.create(
        model=config.ANTHROPIC_MODEL,
        max_tokens=config.MAX_OUTPUT_TOKENS,
        system=PROMPT,
        messages=[{"role": "user", "content": content}],
    )
    return "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")


# --- Gemini ---------------------------------------------------------------

def _gemini_client():
    from google import genai
    # AI Studio ключи: и классические "AIza…", и новые "AQ.…".
    return genai.Client(api_key=config.GEMINI_API_KEY)


def _gemini_generate(parts: list[dict]) -> str:
    from google.genai import types

    if not config.GEMINI_API_KEY:
        raise LLMError("Не задан GEMINI_API_KEY")

    client = _gemini_client()

    contents = []
    for p in parts:
        if p["type"] == "text":
            contents.append(types.Part.from_text(text=p["text"]))
        elif p["type"] == "image":
            contents.append(types.Part.from_bytes(data=p["data"], mime_type=p["mime"]))
        elif p["type"] == "pdf":
            contents.append(types.Part.from_bytes(data=p["data"], mime_type="application/pdf"))
    if not contents:
        raise LLMError("Пустой ввод")

    resp = client.models.generate_content(
        model=config.GEMINI_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=PROMPT,
            response_mime_type="application/json",
            max_output_tokens=config.MAX_OUTPUT_TOKENS,
        ),
    )
    return resp.text or ""


# --- Публичный API --------------------------------------------------------

def generate_cards(parts: list[dict], provider: str, deck_hint: str | None = None) -> list[dict]:
    """Сгенерировать карточки из частей ввода. Бросает LLMError при проблемах."""
    provider = config.resolve_provider(provider)
    if provider == "gemini":
        raw = _gemini_generate(parts)
    else:
        raw = _claude_generate(parts)

    cards = _normalize_cards(_extract_json_array(raw), deck_hint)
    if not cards:
        raise LLMError("Модель не вернула ни одной карточки")
    return cards
