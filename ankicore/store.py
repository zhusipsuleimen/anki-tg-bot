"""SQLite-хранилище для мини-приложения: генерации, карточки, прогресс study.

Одна «генерация» — партия карточек с колодой, созданная пользователем через
мини-приложение. Карточки хранят состояние повторения (known/reviews), чтобы
режим «Повторять» работал прямо в Telegram.

Все операции изолированы по user_id (id Telegram-пользователя): чужие карточки
не видны и не редактируются.
"""
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone

from . import config

_init_lock = threading.Lock()
_initialized = False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def _conn():
    con = sqlite3.connect(config.DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA foreign_keys=ON")
        yield con
        con.commit()
    finally:
        con.close()


def init_db() -> None:
    global _initialized
    if _initialized:
        return
    with _init_lock:
        if _initialized:
            return
        with _conn() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS generations (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id    INTEGER NOT NULL,
                    deck       TEXT    NOT NULL,
                    provider   TEXT,
                    created_at TEXT    NOT NULL
                );
                CREATE TABLE IF NOT EXISTS cards (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    gen_id      INTEGER NOT NULL REFERENCES generations(id) ON DELETE CASCADE,
                    ord         INTEGER NOT NULL,
                    front       TEXT    NOT NULL,
                    back        TEXT    NOT NULL,
                    known       INTEGER NOT NULL DEFAULT 0,
                    reviews     INTEGER NOT NULL DEFAULT 0,
                    last_review TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_gen_user  ON generations(user_id);
                CREATE INDEX IF NOT EXISTS idx_cards_gen ON cards(gen_id);
                """
            )
        _initialized = True


# --- Сериализация ---------------------------------------------------------

def _card_row(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"],
        "ord": r["ord"],
        "front": r["front"],
        "back": r["back"],
        "known": bool(r["known"]),
        "reviews": r["reviews"],
    }


def _gen_summary(r: sqlite3.Row) -> dict:
    return {
        "id": r["id"],
        "deck": r["deck"],
        "provider": r["provider"],
        "created_at": r["created_at"],
        "count": r["count"],
        "known": r["known"],
    }


# --- Запись ---------------------------------------------------------------

def add_generation(user_id: int, deck: str, provider: str | None,
                   cards: list[dict]) -> int:
    """Сохранить новую генерацию с карточками. Вернуть её id."""
    init_db()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO generations(user_id, deck, provider, created_at) "
            "VALUES (?,?,?,?)",
            (user_id, deck, provider, _now()),
        )
        gen_id = cur.lastrowid
        con.executemany(
            "INSERT INTO cards(gen_id, ord, front, back) VALUES (?,?,?,?)",
            [(gen_id, i, c["front"], c["back"]) for i, c in enumerate(cards)],
        )
    return gen_id


def update_card(user_id: int, card_id: int, front: str, back: str) -> bool:
    init_db()
    with _conn() as con:
        cur = con.execute(
            """UPDATE cards SET front=?, back=?
               WHERE id=? AND gen_id IN (
                   SELECT id FROM generations WHERE user_id=?)""",
            (front, back, card_id, user_id),
        )
        return cur.rowcount > 0


def delete_card(user_id: int, card_id: int) -> bool:
    init_db()
    with _conn() as con:
        cur = con.execute(
            """DELETE FROM cards
               WHERE id=? AND gen_id IN (
                   SELECT id FROM generations WHERE user_id=?)""",
            (card_id, user_id),
        )
        return cur.rowcount > 0


def review_card(user_id: int, card_id: int, known: bool) -> bool:
    """Отметить карточку как «знаю/не знаю» в режиме повторения."""
    init_db()
    with _conn() as con:
        cur = con.execute(
            """UPDATE cards
               SET known=?, reviews=reviews+1, last_review=?
               WHERE id=? AND gen_id IN (
                   SELECT id FROM generations WHERE user_id=?)""",
            (1 if known else 0, _now(), card_id, user_id),
        )
        return cur.rowcount > 0


def reset_progress(user_id: int, gen_id: int) -> bool:
    """Сбросить прогресс повторения у всех карточек генерации."""
    init_db()
    with _conn() as con:
        cur = con.execute(
            """UPDATE cards SET known=0, reviews=0, last_review=NULL
               WHERE gen_id=? AND gen_id IN (
                   SELECT id FROM generations WHERE user_id=?)""",
            (gen_id, user_id),
        )
        return cur.rowcount > 0


def delete_generation(user_id: int, gen_id: int) -> bool:
    init_db()
    with _conn() as con:
        cur = con.execute(
            "DELETE FROM generations WHERE id=? AND user_id=?",
            (gen_id, user_id),
        )
        return cur.rowcount > 0


# --- Чтение ---------------------------------------------------------------

def list_generations(user_id: int) -> list[dict]:
    init_db()
    with _conn() as con:
        rows = con.execute(
            """SELECT g.id, g.deck, g.provider, g.created_at,
                      COUNT(c.id) AS count,
                      COALESCE(SUM(c.known), 0) AS known
               FROM generations g
               LEFT JOIN cards c ON c.gen_id = g.id
               WHERE g.user_id = ?
               GROUP BY g.id
               ORDER BY g.id DESC""",
            (user_id,),
        ).fetchall()
    return [_gen_summary(r) for r in rows]


def get_generation(user_id: int, gen_id: int) -> dict | None:
    init_db()
    with _conn() as con:
        g = con.execute(
            "SELECT * FROM generations WHERE id=? AND user_id=?",
            (gen_id, user_id),
        ).fetchone()
        if not g:
            return None
        cards = con.execute(
            "SELECT * FROM cards WHERE gen_id=? ORDER BY ord, id",
            (gen_id,),
        ).fetchall()
    cards = [_card_row(c) for c in cards]
    return {
        "id": g["id"],
        "deck": g["deck"],
        "provider": g["provider"],
        "created_at": g["created_at"],
        "count": len(cards),
        "known": sum(1 for c in cards if c["known"]),
        "cards": cards,
    }


def stats(user_id: int) -> dict:
    init_db()
    with _conn() as con:
        row = con.execute(
            """SELECT
                 (SELECT COUNT(*) FROM generations WHERE user_id=?) AS generations,
                 COUNT(c.id) AS cards,
                 COALESCE(SUM(c.known), 0) AS known
               FROM cards c
               JOIN generations g ON g.id = c.gen_id
               WHERE g.user_id=?""",
            (user_id, user_id),
        ).fetchone()
    return {
        "generations": row["generations"] or 0,
        "cards": row["cards"] or 0,
        "known": row["known"] or 0,
    }
