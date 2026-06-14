"""Веб-сервер мини-приложения (Telegram Mini App).

Отдаёт статику мини-апп и JSON-API. Аутентификация — по `initData`, который
Telegram передаёт веб-приложению: его подпись проверяется HMAC-ом на токене
бота, поэтому подделать user_id нельзя.

Запускается внутри облачного бота (bot_cloud.py) на том же процессе, что и
long-polling, — мини-апп и API живут в одном деплое.
"""
import hashlib
import hmac
import json
import os
from urllib.parse import parse_qsl

from . import config, store
from .apkg import create_apkg
from .flow import CLAUDE_MODELS, provider_label_for
from .inputs import file_to_parts
from .llm import LLMError, generate_cards

WEBAPP_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "webapp")


# --- Проверка подписи Telegram -------------------------------------------

def validate_init_data(init_data: str, bot_token: str, max_age: int = 86400) -> dict:
    """Проверить подпись initData и вернуть объект user (dict с 'id', ...).

    Бросает ValueError, если подпись неверна или данных нет.
    Алгоритм — из документации Telegram (Validating data received via Mini App).
    """
    if not init_data:
        raise ValueError("empty init data")
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    except ValueError as e:
        raise ValueError(f"cannot parse init data: {e}")

    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise ValueError("no hash in init data")

    data_check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calc_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc_hash, received_hash):
        raise ValueError("bad signature")

    try:
        user = json.loads(pairs.get("user", "{}"))
    except json.JSONDecodeError:
        raise ValueError("bad user json")
    if not user.get("id"):
        raise ValueError("no user id")
    return user


def _deck_name(deck: str | None, subdeck: str | None) -> str | None:
    deck = (deck or "").strip()
    subdeck = (subdeck or "").strip()
    if deck and subdeck:
        return f"{deck}::{subdeck}"
    return deck or None


def _provider_and_model(payload: dict) -> tuple[str, str | None, str]:
    """Разобрать выбор модели из мини-апп. Возвращает (provider, model, label).

    payload['model'] — одно из: 'gemini' | 'haiku' | 'sonnet' (или конкретная
    модель Claude). Если не задано — берутся значения по умолчанию из config.
    """
    choice = (payload.get("model") or "").strip().lower()
    if choice == "gemini":
        provider = config.resolve_provider("gemini")
        return provider, None, provider_label_for(provider, None)
    if choice in CLAUDE_MODELS:  # haiku / sonnet
        model = CLAUDE_MODELS[choice]
    elif choice.startswith("claude-"):
        model = choice
    else:
        model = None
    provider = config.resolve_provider("claude")
    if provider != "claude":  # ключа Claude нет — откатываемся на доступный
        return provider, None, provider_label_for(provider, None)
    return provider, model, provider_label_for("claude", model or config.ANTHROPIC_MODEL)


# --- Сборка приложения ----------------------------------------------------

def create_web_app(bot, token: str):
    """Создать FastAPI-приложение мини-апп. bot — telegram.Bot для отправки .apkg."""
    import asyncio

    from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile
    from fastapi.responses import JSONResponse
    from fastapi.staticfiles import StaticFiles

    MAX_UPLOAD = 25 * 1024 * 1024  # 25 МБ

    store.init_db()
    app = FastAPI(title="AnkiCloud Mini App", docs_url=None, redoc_url=None)

    async def current_user(authorization: str = Header(default="")) -> dict:
        prefix = "tma "
        init = authorization[len(prefix):].strip() if authorization.lower().startswith(prefix) else ""
        try:
            return validate_init_data(init, token)
        except ValueError:
            raise HTTPException(status_code=401, detail="invalid init data")

    @app.get("/api/config")
    async def api_config(user: dict = Depends(current_user)):
        providers = config.available_providers()
        models = []
        if "claude" in providers:
            models += [
                {"id": "haiku", "label": "Claude Haiku", "hint": "быстро, дёшево"},
                {"id": "sonnet", "label": "Claude Sonnet", "hint": "умнее, дороже"},
            ]
        if "gemini" in providers:
            models.append({"id": "gemini", "label": "Gemini", "hint": "бесплатный лимит"})
        default = "haiku" if "claude" in providers else ("gemini" if "gemini" in providers else None)
        return {
            "user": {"id": user["id"], "first_name": user.get("first_name", "")},
            "models": models,
            "default_model": default,
        }

    async def _generate_and_store(user_id: int, parts: list[dict], deck_hint, model_choice):
        if not config.available_providers():
            raise HTTPException(status_code=503, detail="не настроен ни один LLM-ключ")
        provider, model, label = _provider_and_model({"model": model_choice})
        try:
            cards = await asyncio.to_thread(generate_cards, parts, provider, deck_hint, model)
        except LLMError as e:
            raise HTTPException(status_code=422, detail=str(e))
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"ошибка генерации: {e}")
        deck = deck_hint or cards[0]["deck"]
        gen_id = await asyncio.to_thread(store.add_generation, user_id, deck, label, cards)
        return store.get_generation(user_id, gen_id)

    @app.post("/api/generate")
    async def api_generate(payload: dict, user: dict = Depends(current_user)):
        text = (payload.get("text") or "").strip()
        if not text:
            raise HTTPException(status_code=400, detail="пустой текст")
        deck_hint = _deck_name(payload.get("deck"), payload.get("subdeck"))
        parts = [{"type": "text", "text": text}]
        return await _generate_and_store(user["id"], parts, deck_hint, payload.get("model"))

    @app.post("/api/generate_file")
    async def api_generate_file(
        file: UploadFile = File(...),
        deck: str = Form(""),
        subdeck: str = Form(""),
        model: str = Form(""),
        text: str = Form(""),
        user: dict = Depends(current_user),
    ):
        data = await file.read()
        if not data:
            raise HTTPException(status_code=400, detail="пустой файл")
        if len(data) > MAX_UPLOAD:
            raise HTTPException(status_code=413, detail="файл больше 25 МБ")
        parts, note = await file_to_parts(data, file.filename or "", file.content_type or "")
        if text.strip():
            parts.append({"type": "text", "text": text.strip()})
        if not parts:
            raise HTTPException(status_code=422, detail=note or "не удалось извлечь содержимое файла")
        deck_hint = _deck_name(deck, subdeck)
        result = await _generate_and_store(user["id"], parts, deck_hint, model)
        if note:
            result["note"] = note
        return result

    @app.get("/api/generations")
    async def api_generations(user: dict = Depends(current_user)):
        return {
            "stats": store.stats(user["id"]),
            "generations": store.list_generations(user["id"]),
        }

    @app.get("/api/generations/{gen_id}")
    async def api_generation(gen_id: int, user: dict = Depends(current_user)):
        gen = store.get_generation(user["id"], gen_id)
        if not gen:
            raise HTTPException(status_code=404, detail="не найдено")
        return gen

    @app.patch("/api/cards/{card_id}")
    async def api_update_card(card_id: int, payload: dict, user: dict = Depends(current_user)):
        front = (payload.get("front") or "").strip()
        back = (payload.get("back") or "").strip()
        if not front or not back:
            raise HTTPException(status_code=400, detail="вопрос и ответ не могут быть пустыми")
        if not store.update_card(user["id"], card_id, front, back):
            raise HTTPException(status_code=404, detail="не найдено")
        return {"ok": True}

    @app.delete("/api/cards/{card_id}")
    async def api_delete_card(card_id: int, user: dict = Depends(current_user)):
        if not store.delete_card(user["id"], card_id):
            raise HTTPException(status_code=404, detail="не найдено")
        return {"ok": True}

    @app.post("/api/cards/{card_id}/review")
    async def api_review_card(card_id: int, payload: dict, user: dict = Depends(current_user)):
        known = bool(payload.get("known"))
        if not store.review_card(user["id"], card_id, known):
            raise HTTPException(status_code=404, detail="не найдено")
        return {"ok": True}

    @app.post("/api/generations/{gen_id}/reset")
    async def api_reset(gen_id: int, user: dict = Depends(current_user)):
        store.reset_progress(user["id"], gen_id)
        return {"ok": True}

    @app.delete("/api/generations/{gen_id}")
    async def api_delete_gen(gen_id: int, user: dict = Depends(current_user)):
        if not store.delete_generation(user["id"], gen_id):
            raise HTTPException(status_code=404, detail="не найдено")
        return {"ok": True}

    @app.post("/api/generations/{gen_id}/send")
    async def api_send(gen_id: int, user: dict = Depends(current_user)):
        """Собрать .apkg и отправить пользователю в чат файлом."""
        gen = store.get_generation(user["id"], gen_id)
        if not gen:
            raise HTTPException(status_code=404, detail="не найдено")
        cards = [{"front": c["front"], "back": c["back"]} for c in gen["cards"]]
        if not cards:
            raise HTTPException(status_code=400, detail="нет карточек")

        deck = gen["deck"]
        path = await asyncio.to_thread(create_apkg, cards, deck)
        try:
            with open(path, "rb") as f:
                await bot.send_document(
                    chat_id=user["id"],
                    document=f,
                    filename=f"{deck.replace('::', '_')}.apkg",
                    caption=(
                        f"✅ {len(cards)} карточек · 📚 {deck}\n"
                        f"Открой файл в Anki → импорт → синхронизация."
                    ),
                )
        finally:
            os.unlink(path)
        return {"ok": True, "count": len(cards)}

    @app.exception_handler(HTTPException)
    async def http_exc(request, exc: HTTPException):
        return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})

    # Статика мини-апп (index.html отдаётся на "/"). Монтируется ПОСЛЕ API,
    # поэтому /api/* не перехватывается.
    app.mount("/", StaticFiles(directory=WEBAPP_DIR, html=True), name="webapp")
    return app
