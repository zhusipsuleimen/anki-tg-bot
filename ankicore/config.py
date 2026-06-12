"""Конфигурация из переменных окружения (.env подхватывается автоматически)."""
import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Пустой ANTHROPIC_BASE_URL, унаследованный из окружения, сломал бы anthropic SDK
# (он попытается использовать "" как адрес). Если значение пустое — убираем,
# чтобы SDK шёл на дефолтный api.anthropic.com.
if os.environ.get("ANTHROPIC_BASE_URL", "x").strip() == "":
    os.environ.pop("ANTHROPIC_BASE_URL", None)


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value or None


# --- Telegram -------------------------------------------------------------
# Для одновременного запуска двух ботов нужны ДВА разных токена от @BotFather.
# Если задан только TELEGRAM_TOKEN — оба бота используют его (но запускать
# одновременно нельзя: Telegram разрешает один long-poll на токен).
TELEGRAM_TOKEN = _clean(os.environ.get("TELEGRAM_TOKEN"))
TELEGRAM_TOKEN_LOCAL = _clean(os.environ.get("TELEGRAM_TOKEN_LOCAL")) or TELEGRAM_TOKEN
TELEGRAM_TOKEN_CLOUD = _clean(os.environ.get("TELEGRAM_TOKEN_CLOUD")) or TELEGRAM_TOKEN

# --- LLM провайдер --------------------------------------------------------
# claude | gemini — какой провайдер по умолчанию (можно переключать в чате).
DEFAULT_PROVIDER = (_clean(os.environ.get("LLM_PROVIDER")) or "claude").lower()

ANTHROPIC_API_KEY = _clean(os.environ.get("ANTHROPIC_API_KEY"))
ANTHROPIC_MODEL = _clean(os.environ.get("ANTHROPIC_MODEL")) or "claude-haiku-4-5"

GEMINI_API_KEY = _clean(os.environ.get("GEMINI_API_KEY")) or _clean(os.environ.get("GOOGLE_API_KEY"))
GEMINI_MODEL = _clean(os.environ.get("GEMINI_MODEL")) or "gemini-2.5-flash"

# --- Голосовые сообщения (транскрипция через Groq Whisper) ----------------
GROQ_API_KEY = _clean(os.environ.get("GROQ_API_KEY"))
GROQ_WHISPER_MODEL = _clean(os.environ.get("GROQ_WHISPER_MODEL")) or "whisper-large-v3-turbo"

# --- AnkiConnect (локальный бот) ------------------------------------------
ANKICONNECT_URL = _clean(os.environ.get("ANKICONNECT_URL")) or "http://localhost:8765"

# --- Поведение генерации --------------------------------------------------
MAX_OUTPUT_TOKENS = int(os.environ.get("MAX_OUTPUT_TOKENS", "8000"))


def available_providers() -> list[str]:
    providers = []
    if ANTHROPIC_API_KEY:
        providers.append("claude")
    if GEMINI_API_KEY:
        providers.append("gemini")
    return providers


def resolve_provider(requested: str | None) -> str:
    """Вернуть рабочий провайдер: запрошенный, если для него есть ключ,
    иначе первый доступный."""
    avail = available_providers()
    if requested and requested in avail:
        return requested
    if DEFAULT_PROVIDER in avail:
        return DEFAULT_PROVIDER
    return avail[0] if avail else (requested or DEFAULT_PROVIDER)
