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
import time

from . import config
from .prompt import PROMPT


class LLMError(Exception):
    pass


# Признаки временной перегрузки провайдера — на них повторяем запрос.
_RETRYABLE = ("503", "unavailable", "overloaded", "429", "resource_exhausted",
              "high demand", "try again")
_OVERLOADED_MSG = (
    "Модель сейчас перегружена (временный всплеск спроса). Попробуй ещё раз "
    "через минуту или переключи модель в выборе выше."
)


def _is_retryable(err: Exception) -> bool:
    msg = str(err).lower()
    return any(s in msg for s in _RETRYABLE)


TRUNCATED_MSG = (
    "Материал слишком большой — ответ обрезался по лимиту токенов. "
    "Пришли его меньшими частями (например, по разделам)."
)

# JSON-схема карточек. Заставляет модель возвращать строго валидный JSON
# (никаких «сырых» переводов строк, оборванных кавычек, markdown-обёрток).
_CARD_PROPS = {
    "front": {"type": "string"},
    "back": {"type": "string"},
    "deck": {"type": "string"},
}
CLAUDE_FORMAT = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "cards": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": _CARD_PROPS,
                    "required": ["front", "back", "deck"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["cards"],
        "additionalProperties": False,
    },
}


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

def _claude_generate(parts: list[dict], model: str | None = None) -> str:
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

    # Стримим: при большом max_tokens SDK требует streaming (иначе ValueError
    # про «operations that may take longer than 10 minutes»).
    with client.messages.stream(
        model=model or config.ANTHROPIC_MODEL,
        max_tokens=config.MAX_OUTPUT_TOKENS,
        system=PROMPT,
        messages=[{"role": "user", "content": content}],
        output_config={"format": CLAUDE_FORMAT},
    ) as stream:
        resp = stream.get_final_message()
    if resp.stop_reason == "max_tokens":
        raise LLMError(TRUNCATED_MSG)
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

    schema = types.Schema(
        type=types.Type.ARRAY,
        items=types.Schema(
            type=types.Type.OBJECT,
            properties={
                "front": types.Schema(type=types.Type.STRING),
                "back": types.Schema(type=types.Type.STRING),
                "deck": types.Schema(type=types.Type.STRING),
            },
            required=["front", "back", "deck"],
        ),
    )
    resp = client.models.generate_content(
        model=config.GEMINI_MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=PROMPT,
            response_mime_type="application/json",
            response_schema=schema,
            max_output_tokens=config.MAX_OUTPUT_TOKENS,
        ),
    )
    cand = (resp.candidates or [None])[0]
    if cand is not None and getattr(getattr(cand, "finish_reason", None), "name", "") == "MAX_TOKENS":
        raise LLMError(TRUNCATED_MSG)
    return resp.text or ""


# --- Публичный API --------------------------------------------------------

# Паузы между повторами при временной перегрузке провайдера (сек).
_RETRY_DELAYS = (2, 5, 10)


def _generate_with_retry(call):
    """Вызвать генерацию с повтором при временной перегрузке (503/429/overloaded).

    Наши собственные LLMError (нет ключа, пустой ввод, обрезка) не повторяем.
    Если все попытки упёрлись в перегрузку — отдаём понятное сообщение.
    """
    last = None
    for delay in (0, *_RETRY_DELAYS):
        if delay:
            time.sleep(delay)
        try:
            return call()
        except LLMError:
            raise
        except Exception as e:  # ошибки SDK провайдера
            last = e
            if not _is_retryable(e):
                raise LLMError(f"ошибка генерации: {e}")
    raise LLMError(_OVERLOADED_MSG) from last


def generate_cards(parts: list[dict], provider: str, deck_hint: str | None = None,
                   model: str | None = None) -> list[dict]:
    """Сгенерировать карточки из частей ввода. Бросает LLMError при проблемах.

    model — конкретная модель Claude (haiku/sonnet); для Gemini игнорируется.
    """
    provider = config.resolve_provider(provider)
    if provider == "gemini":
        raw = _generate_with_retry(lambda: _gemini_generate(parts))
    else:
        raw = _generate_with_retry(lambda: _claude_generate(parts, model))

    cards = _normalize_cards(_extract_json_array(raw), deck_hint)
    if not cards:
        raise LLMError("Не нашёл учебного материала для карточек — пришли конспект или текст по теме.")
    return cards
