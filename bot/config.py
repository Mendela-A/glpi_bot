import os

from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Змінні середовища
# ---------------------------------------------------------------------------

BOT_TOKEN: str = os.environ["BOT_TOKEN"]
TECHNICIANS_CHAT_ID: int = int(os.environ["TECHNICIANS_CHAT_ID"])
GLPI_URL: str = os.environ["GLPI_URL"].rstrip("/")
GLPI_EXTERNAL_URL: str = os.environ.get("GLPI_EXTERNAL_URL", "http://localhost:8080").rstrip("/")
GLPI_APP_TOKEN: str = os.environ["GLPI_APP_TOKEN"]
GLPI_USER_TOKEN: str = os.environ["GLPI_USER_TOKEN"]
REDIS_URL: str = os.environ.get("REDIS_URL", "redis://redis:6379/0")

ALLOWED_GROUP_ID: int | None = int(os.environ["ALLOWED_GROUP_ID"]) if os.environ.get("ALLOWED_GROUP_ID") else None
ADMIN_CHAT_ID: int | None = int(os.environ["ADMIN_CHAT_ID"]) if os.environ.get("ADMIN_CHAT_ID") else None

# ---------------------------------------------------------------------------
# Константи
# ---------------------------------------------------------------------------

TICKET_STATUS_CLOSED = 6
POLL_INTERVAL_SEC = 300  # 5 хвилин
TICKETS_PER_PAGE = 5

# Поля пошуку GLPI (field IDs у відповіді search API для Ticket)
GLPI_FIELD_ID = "2"
GLPI_FIELD_NAME = "1"
GLPI_FIELD_STATUS = "12"
GLPI_FIELD_DATE = "19"
GLPI_FIELD_CONTENT = "21"

PRIORITIES: dict[str, int] = {
    "🟢 Низький": 2,
    "🟡 Середній": 3,
    "🔴 Високий": 4,
}

TICKET_STATUSES: dict[int, str] = {
    1: "🆕 Нова",
    2: "🔧 В роботі",
    3: "🔧 В роботі",
    4: "⏳ Очікує",
    5: "✅ Вирішена",
    6: "🔒 Закрита",
}

STATUS_NOTIFY_MESSAGES: dict[int, str] = {
    2: "🔧 Вашу заявку {ticket} взято в роботу.",
    5: "✅ Вашу заявку {ticket} вирішено. Очікуйте підтвердження або закриття.",
    6: "🔒 Вашу заявку {ticket} закрито.",
}

CATEGORIES_TTL: int = 86400  # 24 години

# Завантажується динамічно з GLPI при старті
CATEGORIES: dict[str, int] = {}
