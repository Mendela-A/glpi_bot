# GLPI Telegram Bot

Telegram-бот для створення заявок у GLPI (IT Help Desk) через FSM-форму.
Написаний на Python 3.11 + aiogram 3.7, запускається у Docker Compose.

---

## Стек

| Компонент | Технологія |
|-----------|-----------|
| Telegram-фреймворк | aiogram 3.7 (async) |
| HTTP-клієнт | aiohttp |
| FSM storage | Redis (aiogram RedisStorage) |
| GLPI API | REST (`/apirest.php/`) |
| Контейнеризація | Docker Compose |

---

## Структура проєкту

```
glpi_bot/
├── docker-compose.yml       # mariadb + glpi + redis + bot
├── .env                     # змінні середовища (не в git)
├── CLAUDE.md
└── bot/
    ├── Dockerfile
    ├── requirements.txt
    ├── main.py              # точка входу: startup, middleware, polling
    ├── config.py            # всі env vars і константи (CATEGORIES мутується при старті)
    ├── states.py            # TicketForm, FollowupForm (aiogram StatesGroup)
    ├── glpi_client.py       # GLPIClient — всі виклики GLPI REST API
    ├── services.py          # синглтони: bot, dp, glpi
    ├── keyboards.py         # ReplyKeyboard + InlineKeyboard builder-функції
    ├── middleware.py        # авторизація (getChatMember) + rate limiting
    ├── polling.py           # кеш активних тікетів + polling loop
    └── handlers/
        ├── __init__.py      # агрегує всі роутери
        ├── common.py        # /start, /cancel, fallback, error handler
        ├── ticket_form.py   # FSM-флоу створення заявки
        ├── tickets.py       # список, пагінація, деталі, скасування
        └── followup.py      # відповідь користувача на коментар техніка
```

---

## Запуск локально

```bash
cp .env.example .env   # заповнити змінні
docker compose up --build
```

Або безпосередньо:
```bash
cd bot
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
python main.py
```

---

## Змінні середовища (`.env`)

| Змінна | Обов'язкова | Опис |
|--------|-------------|------|
| `BOT_TOKEN` | ✅ | Токен Telegram-бота |
| `TECHNICIANS_CHAT_ID` | ✅ | ID чату/групи техніків для сповіщень про нові заявки |
| `GLPI_URL` | ✅ | Внутрішня URL GLPI (напр. `http://glpi_app`) |
| `GLPI_EXTERNAL_URL` | — | Публічна URL GLPI для посилань у повідомленнях |
| `GLPI_APP_TOKEN` | ✅ | App-Token GLPI API |
| `GLPI_USER_TOKEN` | ✅ | User-Token GLPI API (user_token) |
| `REDIS_URL` | — | `redis://redis:6379/0` за замовчуванням |
| `ALLOWED_GROUP_ID` | — | ID групи; якщо задано — тільки члени групи мають доступ |
| `ADMIN_CHAT_ID` | — | ID чату для сповіщень про необроблені помилки |
| `MARIADB_*` | ✅ | Змінні для MariaDB (використовує GLPI) |

---

## Ключові патерни

### FSM-флоу створення заявки
Стани `TicketForm`: `category → description → priority → photo → phone → confirm`.
Визначені у `states.py`. Обробники у `handlers/ticket_form.py`.
На кроці `confirm:yes` заявка надсилається до GLPI і реєструється у `_bot_tickets`.

### Polling статусів і follow-up
`polling.py` веде словник `_bot_tickets: {ticket_id: user_id}` усіх активних
бот-заявок. Кожні `POLL_INTERVAL_SEC` (300 с) перевіряє:
- `/Ticket/{id}` — зміна статусу → Telegram-сповіщення
- `/Ticket/{id}/ITILFollowup` — нові коментарі → Telegram-повідомлення + кнопка ✏️ Відповісти

При старті `_load_bot_tickets()` відновлює кеш із GLPI за мітками `[tg:USER_ID]` у content.

### Авторизація
Якщо `ALLOWED_GROUP_ID` задано — middleware перевіряє членство через `getChatMember`
з кешем 5 хв (`_member_cache`). Rate limit: 10 запитів / 60 с, вимкнений під час
активних FSM-станів.

### Безпека IDOR
Перед скасуванням заявки `cancel_ticket_confirm` перевіряє, що `ticket_id` є
у списку заявок поточного користувача (запит через `[tg:USER_ID]`).

### Теггування заявок
Кожна заявка містить `[tg:USER_ID]` у полі `content` — за цим тегом бот
знаходить заявки користувача через GLPI Search API.

---

## Додавання нового хендлера

1. Створіть або відредагуйте файл у `bot/handlers/`.
2. Визначте `router = Router()` у файлі.
3. Зареєструйте роутер у `handlers/__init__.py` через `router.include_router(...)`.
   Якщо хендлер є catch-all (без фільтрів) — додавайте його **після** специфічних.

## Залежності між модулями

```
config ← (нічого)
states ← (нічого)
glpi_client ← config
services ← config, glpi_client
keyboards ← config
middleware ← config, services
polling ← config, services, keyboards
handlers/* ← services, keyboards, states, config, polling
main ← services, middleware, polling, handlers
```
