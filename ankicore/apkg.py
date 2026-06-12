"""Сборка .apkg файла из карточек (для облачного бота)."""
import random
import tempfile

import genanki

from .prompt import CARD_CSS, MODEL_NAME, QFMT, AFMT


def _model() -> genanki.Model:
    # Фиксированный model_id, чтобы импорты не плодили дубли note type в Anki.
    return genanki.Model(
        1607392319,
        MODEL_NAME,
        fields=[{"name": "Front"}, {"name": "Back"}],
        templates=[{"name": "Card 1", "qfmt": QFMT, "afmt": AFMT}],
        css=CARD_CSS,
    )


def create_apkg(cards: list[dict], deck_name: str) -> str:
    """Создать .apkg, вернуть путь к временному файлу."""
    model = _model()
    deck = genanki.Deck(random.randrange(1 << 30, 1 << 31), deck_name)
    for card in cards:
        deck.add_note(genanki.Note(model=model, fields=[card["front"], card["back"]]))

    tmp = tempfile.NamedTemporaryFile(suffix=".apkg", delete=False)
    tmp.close()
    genanki.Package(deck).write_to_file(tmp.name)
    return tmp.name
