"""Офлайн-проверка мини-приложения: подпись initData, store, API.

Сеть не используется — LLM и отправка .apkg замоканы. Запуск:
    .venv/bin/python tests_webapp.py
"""
import hashlib
import hmac
import json
import os
import tempfile
from urllib.parse import urlencode

import ankicore.config as config

# Изолированная временная БД ДО первого обращения к store.
config.DB_PATH = os.path.join(tempfile.gettempdir(), "test_ankicloud.db")
for ext in ("", "-wal", "-shm"):
    try:
        os.remove(config.DB_PATH + ext)
    except FileNotFoundError:
        pass
config.ANTHROPIC_API_KEY = "x-test-key"  # чтобы провайдер claude был доступен

import ankicore.webapp as webapp  # noqa: E402

TOKEN = "123456:TESTTOKEN"


def make_init(user_id: int = 42) -> str:
    user = {"id": user_id, "first_name": "Test"}
    params = {"auth_date": "1700000000", "user": json.dumps(user, separators=(",", ":"))}
    dcs = "\n".join(f"{k}={params[k]}" for k in sorted(params))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    h = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode({**params, "hash": h})


def main():
    init = make_init()

    # 1. Подпись initData
    assert webapp.validate_init_data(init, TOKEN)["id"] == 42
    for bad in (init + "x", init):  # подделка / неверный токен
        try:
            webapp.validate_init_data(bad, "999:WRONG" if bad is init else TOKEN)
            raise AssertionError("должно было отклонить")
        except ValueError:
            pass
    print("[1] подпись initData: валидный принят, подделка/чужой токен отклонены ОК")

    # Мок LLM и бота
    webapp.generate_cards = lambda parts, provider, deck_hint, model: [
        {"front": "Q1", "back": "A1", "deck": deck_hint or "Anki"},
        {"front": "Q2", "back": "A2", "deck": deck_hint or "Anki"},
    ]
    sent = {}

    class FakeBot:
        async def send_document(self, chat_id, document, filename, caption):
            sent["chat_id"] = chat_id
            sent["filename"] = filename
            document.read()

    from fastapi.testclient import TestClient
    client = TestClient(webapp.create_web_app(bot=FakeBot(), token=TOKEN))
    H = {"Authorization": "tma " + init}

    # 2. Статика + авторизация
    assert client.get("/").status_code == 200
    assert "AnkiCloud" in client.get("/").text
    assert client.get("/api/config").status_code == 401
    cfg = client.get("/api/config", headers=H).json()
    assert any(m["id"] == "haiku" for m in cfg["models"])
    print("[2] статика отдаётся, /api/config защищён, модели:", [m["id"] for m in cfg["models"]])

    # 3. Генерация → правка → удаление
    gen = client.post("/api/generate", headers=H,
                      json={"text": "x", "deck": "Онко", "subdeck": "РМЖ", "model": "sonnet"}).json()
    assert gen["deck"] == "Онко::РМЖ" and gen["count"] == 2
    gid, c0, c1 = gen["id"], gen["cards"][0]["id"], gen["cards"][1]["id"]
    assert client.patch(f"/api/cards/{c0}", headers=H,
                        json={"front": "новый", "back": "ответ"}).status_code == 200
    assert client.delete(f"/api/cards/{c1}", headers=H).status_code == 200
    g = client.get(f"/api/generations/{gid}", headers=H).json()
    assert g["count"] == 1 and g["cards"][0]["front"] == "новый"
    print("[3] генерация/правка/удаление ОК")

    # 3b. Генерация из загруженного файла (multipart)
    r = client.post("/api/generate_file", headers=H,
                    files={"file": ("конспект.txt", "клетка — единица жизни".encode(), "text/plain")},
                    data={"deck": "Биология", "subdeck": "", "model": "haiku", "text": ""})
    assert r.status_code == 200, (r.status_code, r.text)
    assert r.json()["deck"] == "Биология" and r.json()["count"] == 2
    r = client.post("/api/generate_file", headers=H,
                    files={"file": ("empty.txt", b"", "text/plain")}, data={})
    assert r.status_code == 400, "пустой файл должен дать 400"
    print("[3b] генерация из файла (.txt) ОК, пустой файл отклонён")

    # 4. Повторение + статистика + сброс
    assert client.post(f"/api/cards/{c0}/review", headers=H, json={"known": True}).status_code == 200
    assert client.get("/api/generations", headers=H).json()["stats"]["known"] == 1
    client.post(f"/api/generations/{gid}/reset", headers=H)
    assert client.get("/api/generations", headers=H).json()["stats"]["known"] == 0
    print("[4] повторение/статистика/сброс ОК")

    # 5. Отправка .apkg
    assert client.post(f"/api/generations/{gid}/send", headers=H).status_code == 200
    assert sent["chat_id"] == 42 and sent["filename"] == "Онко_РМЖ.apkg"
    print("[5] .apkg отправлен в чат:", sent["filename"])

    # 6. Изоляция по пользователю
    H2 = {"Authorization": "tma " + make_init(777)}
    assert client.get(f"/api/generations/{gid}", headers=H2).status_code == 404
    assert client.get("/api/generations", headers=H2).json()["generations"] == []
    print("[6] данные изолированы по user_id ОК")

    print("\nВСЕ ПРОВЕРКИ МИНИ-АПП ПРОШЛИ ✅")


if __name__ == "__main__":
    main()
