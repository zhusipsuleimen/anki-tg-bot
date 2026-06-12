"""Клиент AnkiConnect (локальный бот добавляет карточки напрямую в Anki).

Требует запущенный Anki с аддоном AnkiConnect (слушает http://localhost:8765).
"""
import httpx

from . import config
from .prompt import CARD_CSS, MODEL_NAME, QFMT, AFMT


class AnkiConnectError(Exception):
    pass


async def invoke(action: str, **params):
    payload = {"action": action, "version": 6, "params": params}
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            r = await client.post(config.ANKICONNECT_URL, json=payload)
        except httpx.HTTPError as e:
            raise AnkiConnectError(f"Anki недоступен ({config.ANKICONNECT_URL}): {e}")
    data = r.json()
    if data.get("error"):
        raise AnkiConnectError(data["error"])
    return data.get("result")


async def is_available() -> bool:
    try:
        await invoke("version")
        return True
    except AnkiConnectError:
        return False


async def list_decks() -> list[str]:
    return sorted(await invoke("deckNames"))


async def _ensure_model():
    names = await invoke("modelNames")
    if MODEL_NAME in names:
        return
    await invoke(
        "createModel",
        modelName=MODEL_NAME,
        inOrderFields=["Front", "Back"],
        css=CARD_CSS,
        isCloze=False,
        cardTemplates=[{"Name": "Card 1", "Front": QFMT, "Back": AFMT}],
    )


async def _ensure_deck(deck: str):
    await invoke("createDeck", deck=deck)


async def add_cards(cards: list[dict], deck: str) -> tuple[int, int]:
    """Добавить карточки в указанную колоду. Вернуть (добавлено, пропущено_дублей)."""
    await _ensure_model()
    await _ensure_deck(deck)

    notes = [{
        "deckName": deck,
        "modelName": MODEL_NAME,
        "fields": {"Front": c["front"], "Back": c["back"]},
        "options": {"allowDuplicate": False, "duplicateScope": "deck"},
        "tags": ["tg-bot"],
    } for c in cards]

    result = await invoke("addNotes", notes=notes)
    added = sum(1 for nid in result if nid)
    skipped = len(result) - added
    return added, skipped
