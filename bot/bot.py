import asyncio
import html
import logging
import math
import os
import time
from datetime import datetime, timezone

import json
import re
import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command, StateFilter, ChatMemberUpdatedFilter, JOIN_TRANSITION
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.redis import RedisStorage
from aiogram.types import (
    CallbackQuery,
    ChatMemberUpdated,
    ErrorEvent,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    KeyboardButton,
)
from aiogram.utils.markdown import hbold, hcode
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Конфігурація
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

# Завантажується динамічно з GLPI при старті
CATEGORIES: dict[str, int] = {}

TICKET_STATUS_CLOSED = 6
POLL_INTERVAL_SEC = 300  # 5 хвилин
TICKETS_PER_PAGE = 5

# Поля пошуку GLPI (field IDs у відповіді search API для Ticket)
GLPI_FIELD_ID = "2"
GLPI_FIELD_NAME = "1"
GLPI_FIELD_STATUS = "12"
GLPI_FIELD_DATE = "19"
GLPI_FIELD_CONTENT = "21"
GLPI_FIELD_DATE_MOD = "5"   # date_mod Ticket

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

# ---------------------------------------------------------------------------
# FSM
# ---------------------------------------------------------------------------


PRIORITIES: dict[str, int] = {
    "🟢 Низький": 2,
    "🟡 Середній": 3,
    "🔴 Високий": 4,
}


class TicketForm(StatesGroup):
    category    = State()
    description = State()
    priority    = State()
    photo       = State()
    phone       = State()
    confirm     = State()


class FollowupForm(StatesGroup):
    reply = State()


# ---------------------------------------------------------------------------
# GLPI API клієнт
# ---------------------------------------------------------------------------


class GLPIClient:
    def __init__(self) -> None:
        self._session_token: str | None = None
        self._http: aiohttp.ClientSession | None = None
        self._session_lock = asyncio.Lock()

    @property
    def _base_headers(self) -> dict:
        return {
            "App-Token": GLPI_APP_TOKEN,
            "Content-Type": "application/json",
        }

    @property
    def _auth_headers(self) -> dict:
        return {**self._base_headers, "Session-Token": self._session_token or ""}

    async def _get_http(self) -> aiohttp.ClientSession:
        if self._http is None or self._http.closed:
            self._http = aiohttp.ClientSession()
        return self._http

    async def init_session(self) -> None:
        async with self._session_lock:
            http = await self._get_http()
            headers = {**self._base_headers, "Authorization": f"user_token {GLPI_USER_TOKEN}"}
            async with http.get(f"{GLPI_URL}/apirest.php/initSession", headers=headers) as resp:
                resp.raise_for_status()
                data = await resp.json()
                self._session_token = data["session_token"]
                log.info("GLPI session ініціалізовано")

    async def kill_session(self) -> None:
        if not self._session_token:
            return
        http = await self._get_http()
        try:
            async with http.get(
                f"{GLPI_URL}/apirest.php/killSession", headers=self._auth_headers
            ):
                pass
        except Exception:
            pass
        self._session_token = None

    async def _ensure_session(self) -> None:
        if not self._session_token:
            await self.init_session()

    async def _request(self, method: str, url: str, **kwargs) -> aiohttp.ClientResponse:
        """Виконує HTTP-запит з автоматичним retry при 401.

        Увага: не використовувати для multipart/form-data — тіло запиту вже
        буде прочитано і не може бути надіслане повторно.
        """
        await self._ensure_session()
        http = await self._get_http()
        headers = kwargs.pop("headers", self._auth_headers)
        resp = await http.request(method, url, headers=headers, **kwargs)
        if resp.status == 401:
            resp.release()  # звільняємо з'єднання перед повторним запитом
            await self.init_session()
            headers = {**headers, "Session-Token": self._session_token or ""}
            resp = await http.request(method, url, headers=headers, **kwargs)
        return resp

    async def create_ticket(
        self,
        name: str,
        content: str,
        category_id: int,
        telegram_user_id: int,
        phone: str | None = None,
        priority: int = 3,
    ) -> dict:
        extra = f"\nТелефон: {phone}" if phone else ""
        tagged_content = f"{content}{extra}\n\n[tg:{telegram_user_id}]"
        payload = {
            "input": {
                "name": name,
                "content": tagged_content,
                "itilcategories_id": category_id,
                "type": 1,           # 1 = Incident
                "urgency": priority,
                "impact": priority,
                "priority": priority,
                "requesttypes_id": 7,  # Telegram Bot
            }
        }
        async with await self._request("POST", f"{GLPI_URL}/apirest.php/Ticket", json=payload) as resp:
            resp.raise_for_status()
            return await resp.json()

    async def get_user_tickets(
        self, user_id: int, offset: int = 0, limit: int = TICKETS_PER_PAGE
    ) -> tuple[list[dict], int]:
        """Повертає (заявки, totalcount) за міткою [tg:user_id] в content."""
        params = {
            "criteria[0][field]": GLPI_FIELD_CONTENT,
            "criteria[0][searchtype]": "contains",
            "criteria[0][value]": f"[tg:{user_id}]",
            "forcedisplay[0]": GLPI_FIELD_NAME,
            "forcedisplay[1]": GLPI_FIELD_STATUS,
            "forcedisplay[2]": GLPI_FIELD_DATE,
            "sort": GLPI_FIELD_DATE,
            "order": "DESC",
            "range": f"{offset}-{offset + limit - 1}",
        }
        async with await self._request(
            "GET", f"{GLPI_URL}/apirest.php/search/Ticket", params=params
        ) as resp:
            if resp.status in (200, 206):
                data = await resp.json()
                return data.get("data", []), data.get("totalcount", 0)
            return [], 0

    async def get_categories(self) -> dict[str, int]:
        """Повертає словник {назва: id} категорій з GLPI."""
        async with await self._request(
            "GET", f"{GLPI_URL}/apirest.php/ITILCategory", params={"range": "0-200"}
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return {
                item["completename"]: item["id"]
                for item in data
                if isinstance(item, dict)
                and item.get("is_active") != 0
                and item.get("is_helpdeskvisible") == 1
            }

    async def get_tickets_changed_since(self, since: datetime, status: int) -> list[dict]:
        """Заявки з вказаним статусом, змінені після `since`, що містять [tg:."""
        params = {
            "criteria[0][field]": GLPI_FIELD_STATUS,
            "criteria[0][searchtype]": "equals",
            "criteria[0][value]": str(status),
            "criteria[1][field]": GLPI_FIELD_DATE_MOD,
            "criteria[1][searchtype]": "morethan",
            "criteria[1][value]": since.strftime("%Y-%m-%d %H:%M:%S"),
            "criteria[2][field]": GLPI_FIELD_CONTENT,
            "criteria[2][searchtype]": "contains",
            "criteria[2][value]": "[tg:",
            "forcedisplay[0]": GLPI_FIELD_ID,
            "forcedisplay[1]": GLPI_FIELD_NAME,
            "forcedisplay[2]": GLPI_FIELD_STATUS,
            "forcedisplay[3]": GLPI_FIELD_CONTENT,
            "range": "0-50",
        }
        async with await self._request(
            "GET", f"{GLPI_URL}/apirest.php/search/Ticket", params=params
        ) as resp:
            if resp.status in (200, 206):
                return (await resp.json()).get("data", [])
            return []

    async def get_ticket(self, ticket_id: int) -> dict:
        """Повний об'єкт заявки."""
        async with await self._request("GET", f"{GLPI_URL}/apirest.php/Ticket/{ticket_id}") as resp:
            resp.raise_for_status()
            return await resp.json()

    async def get_ticket_followups(self, ticket_id: int) -> list[dict]:
        """Follow-up коментарі до заявки."""
        async with await self._request(
            "GET", f"{GLPI_URL}/apirest.php/Ticket/{ticket_id}/ITILFollowup"
        ) as resp:
            if resp.status in (200, 206):
                data = await resp.json()
                return data if isinstance(data, list) else []
            return []

    async def get_recent_followups(self, since: datetime) -> list[dict]:
        """Follow-ups, створені після `since`.

        Поля відповіді: "2"=id, "3"=items_id (ticket_id), "4"=content, "5"=date_creation.
        """
        params = {
            "criteria[0][field]": "5",   # date_creation ITILFollowup
            "criteria[0][searchtype]": "morethan",
            "criteria[0][value]": since.strftime("%Y-%m-%d %H:%M:%S"),
            "forcedisplay[0]": "2",      # id
            "forcedisplay[1]": "3",      # items_id (ticket_id)
            "forcedisplay[2]": "4",      # content
            "forcedisplay[3]": "5",      # date_creation
            "range": "0-50",
        }
        async with await self._request(
            "GET", f"{GLPI_URL}/apirest.php/search/ITILFollowup", params=params
        ) as resp:
            if resp.status in (200, 206):
                return (await resp.json()).get("data", [])
            return []

    async def add_followup(self, ticket_id: int, content: str) -> None:
        """Додає публічний follow-up до заявки."""
        payload = {"input": {
            "itemtype": "Ticket",
            "items_id": ticket_id,
            "content": content,
            "is_private": 0,
        }}
        async with await self._request(
            "POST", f"{GLPI_URL}/apirest.php/Ticket/{ticket_id}/ITILFollowup", json=payload
        ) as resp:
            resp.raise_for_status()

    async def upload_document(self, file_bytes: bytes, filename: str) -> int:
        """Завантажує файл у GLPI і повертає document id.

        Не використовує _request — FormData не можна надіслати двічі після 401.
        """
        await self._ensure_session()
        http = await self._get_http()
        manifest = json.dumps({"input": {"name": filename, "_filename": [filename]}})
        form = aiohttp.FormData()
        form.add_field("uploadManifest", manifest, content_type="application/json")
        form.add_field("filename[0]", file_bytes, filename=filename, content_type="image/jpeg")
        # Для multipart не передаємо Content-Type — aiohttp встановить boundary сам
        headers = {k: v for k, v in self._auth_headers.items() if k != "Content-Type"}
        async with http.post(
            f"{GLPI_URL}/apirest.php/Document",
            data=form,
            headers=headers,
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
            return data["id"]

    async def link_document_to_ticket(self, doc_id: int, ticket_id: int) -> None:
        """Прив'язує документ до тікету."""
        payload = {"input": {"documents_id": doc_id, "items_id": ticket_id, "itemtype": "Ticket"}}
        async with await self._request(
            "POST", f"{GLPI_URL}/apirest.php/Document_Item", json=payload
        ) as resp:
            resp.raise_for_status()

    async def cancel_ticket(self, ticket_id: int) -> None:
        """Закриває заявку (status=TICKET_STATUS_CLOSED)."""
        async with await self._request(
            "PUT",
            f"{GLPI_URL}/apirest.php/Ticket/{ticket_id}",
            json={"input": {"status": TICKET_STATUS_CLOSED}},
        ) as resp:
            resp.raise_for_status()

    async def close(self) -> None:
        await self.kill_session()
        if self._http and not self._http.closed:
            await self._http.close()


# ---------------------------------------------------------------------------
# Клавіатури
# ---------------------------------------------------------------------------

MAIN_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📝 Створити заявку")],
        [KeyboardButton(text="📋 Мої заявки")],
    ],
    resize_keyboard=True,
)


def categories_keyboard() -> InlineKeyboardMarkup | None:
    if not CATEGORIES:
        return None
    items = [
        InlineKeyboardButton(text=name, callback_data=f"cat:{cat_id}")
        for name, cat_id in CATEGORIES.items()
    ]
    # По 2 кнопки в рядок для компактності
    buttons = [items[i:i + 2] for i in range(0, len(items), 2)]
    buttons.append([InlineKeyboardButton(text="❌ Скасувати", callback_data="form:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


PHONE_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📱 Поділитися номером", request_contact=True)],
        [KeyboardButton(text="⏭ Пропустити")],
        [KeyboardButton(text="❌ Скасувати заявку")],
    ],
    resize_keyboard=True,
)

SKIP_PHOTO_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="⏭ Пропустити фото")],
        [KeyboardButton(text="❌ Скасувати заявку")],
    ],
    resize_keyboard=True,
)


def priority_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=label, callback_data=f"pri:{val}") for label, val in PRIORITIES.items()],
        [InlineKeyboardButton(text="❌ Скасувати", callback_data="form:cancel")],
    ])


def confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Підтвердити", callback_data="confirm:yes"),
                InlineKeyboardButton(text="❌ Скасувати", callback_data="confirm:no"),
            ]
        ]
    )


def tickets_keyboard(
    tickets: list[dict], offset: int, total: int
) -> InlineKeyboardMarkup | None:
    """Inline-клавіатура для списку заявок з пагінацією."""
    rows: list[list[InlineKeyboardButton]] = []
    for ticket in tickets:
        ticket_id = ticket.get(GLPI_FIELD_ID, "?")
        status_id = int(ticket.get(GLPI_FIELD_STATUS, 0))
        row = [InlineKeyboardButton(
            text=f"🔍 #{ticket_id}",
            callback_data=f"tdetail:{ticket_id}",
        )]
        if status_id in (1, 2, 3) and ticket_id != "?":
            row.append(InlineKeyboardButton(
                text=f"🗑 Скасувати",
                callback_data=f"cancel:{ticket_id}",
            ))
        rows.append(row)

    # Навігація
    total_pages = math.ceil(total / TICKETS_PER_PAGE) or 1
    current_page = offset // TICKETS_PER_PAGE + 1
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton(text="← Назад", callback_data=f"mytickets:{offset - TICKETS_PER_PAGE}"))
    nav.append(InlineKeyboardButton(text=f"{current_page}/{total_pages}", callback_data="noop"))
    if offset + TICKETS_PER_PAGE < total:
        nav.append(InlineKeyboardButton(text="Далі →", callback_data=f"mytickets:{offset + TICKETS_PER_PAGE}"))
    if len(nav) > 1:  # показуємо навігацію тільки якщо є куди переходити
        rows.append(nav)

    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


# ---------------------------------------------------------------------------
# Telegram Bot + Dispatcher
# ---------------------------------------------------------------------------

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(storage=RedisStorage.from_url(REDIS_URL))
glpi = GLPIClient()


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

_rate_limit: dict[int, list[float]] = {}
RATE_LIMIT_REQUESTS = 10
RATE_LIMIT_WINDOW = 60  # секунд


def _is_rate_limited(user_id: int) -> bool:
    now = time.monotonic()
    history = [t for t in _rate_limit.get(user_id, []) if now - t < RATE_LIMIT_WINDOW]
    _rate_limit[user_id] = history
    if len(history) >= RATE_LIMIT_REQUESTS:
        return True
    history.append(now)
    return False


# ---------------------------------------------------------------------------
# Middleware авторизації
# ---------------------------------------------------------------------------


_member_cache: dict[int, tuple[bool, float]] = {}
MEMBER_CACHE_TTL = 300  # 5 хвилин


async def is_group_member(user_id: int) -> bool:
    if not ALLOWED_GROUP_ID:
        return True
    now = time.monotonic()
    cached = _member_cache.get(user_id)
    if cached and now < cached[1]:
        return cached[0]
    try:
        member = await bot.get_chat_member(ALLOWED_GROUP_ID, user_id)
        result = member.status not in ("left", "kicked", "banned")
    except Exception:
        result = False
    _member_cache[user_id] = (result, now + MEMBER_CACHE_TTL)
    return result


@dp.message.middleware()
async def message_middleware(handler, event: Message, data: dict):
    if event.chat.type != "private":
        return
    if not await is_group_member(event.from_user.id):
        await event.answer("⛔ Доступ тільки для членів групи.")
        return
    if _is_rate_limited(event.from_user.id):
        await event.answer("⚠️ Занадто багато запитів. Зачекайте хвилину.")
        return
    return await handler(event, data)


@dp.callback_query.middleware()
async def callback_middleware(handler, event: CallbackQuery, data: dict):
    if not await is_group_member(event.from_user.id):
        await event.answer("⛔ Доступ тільки для членів групи.", show_alert=True)
        return
    if _is_rate_limited(event.from_user.id):
        await event.answer("⚠️ Занадто багато запитів.", show_alert=True)
        return
    return await handler(event, data)


# ---------------------------------------------------------------------------
# Хендлери
# ---------------------------------------------------------------------------


@dp.my_chat_member(ChatMemberUpdatedFilter(JOIN_TRANSITION))
async def bot_added_to_group(event: ChatMemberUpdated) -> None:
    pass  # бот входить до групи мовчки


@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        f"Вітаю, {hbold(message.from_user.full_name)}!\n"
        "Я допоможу вам створити заявку в IT-відділ.",
        reply_markup=MAIN_MENU,
    )


@dp.message(Command("cancel"), StateFilter(TicketForm, FollowupForm))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Скасовано.", reply_markup=MAIN_MENU)


@dp.message(Command("cancel"))
async def cmd_cancel_noop(message: Message) -> None:
    await message.answer("Немає активної дії для скасування.", reply_markup=MAIN_MENU)


@dp.message(F.text == "❌ Скасувати заявку", StateFilter(TicketForm))
async def cancel_form_reply(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Заявку скасовано.", reply_markup=MAIN_MENU)


@dp.callback_query(F.data == "form:cancel", StateFilter(TicketForm))
async def cancel_form_inline(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Заявку скасовано.")
    await callback.message.answer("Головне меню:", reply_markup=MAIN_MENU)
    await callback.answer()


@dp.message(F.text == "📝 Створити заявку")
async def start_ticket(message: Message, state: FSMContext) -> None:
    kb = categories_keyboard()
    if kb is None:
        await message.answer("⚠️ Категорії недоступні. Спробуйте пізніше.")
        return
    await state.set_state(TicketForm.category)
    removal = await message.answer(".", reply_markup=ReplyKeyboardRemove())
    await removal.delete()
    await message.answer("Оберіть категорію заявки:", reply_markup=kb)


@dp.callback_query(TicketForm.category, F.data.startswith("cat:"))
async def process_category(callback: CallbackQuery, state: FSMContext) -> None:
    cat_id = int(callback.data.removeprefix("cat:"))
    category_name = next((n for n, i in CATEGORIES.items() if i == cat_id), None)
    if category_name is None:
        await callback.answer("Невідома категорія.", show_alert=True)
        return
    await state.update_data(category=category_name)
    await state.set_state(TicketForm.description)
    await callback.message.edit_text(
        f"Категорія: {hbold(category_name)}\n\nОпишіть вашу проблему:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="❌ Скасувати", callback_data="form:cancel")
        ]]),
    )
    await callback.answer()


@dp.message(TicketForm.description, F.text)
async def process_description(message: Message, state: FSMContext) -> None:
    if len(message.text.strip()) < 5:
        await message.answer("Будь ласка, опишіть проблему детальніше (мінімум 5 символів).")
        return
    await state.update_data(description=message.text.strip())
    await state.set_state(TicketForm.priority)
    await message.answer("Оберіть пріоритет заявки:", reply_markup=priority_keyboard())


@dp.message(TicketForm.description)
async def description_invalid(message: Message) -> None:
    await message.answer("✏️ Введіть текстовий опис проблеми.")


@dp.callback_query(TicketForm.priority, F.data.startswith("pri:"))
async def process_priority(callback: CallbackQuery, state: FSMContext) -> None:
    val = int(callback.data.removeprefix("pri:"))
    label = next((lbl for lbl, v in PRIORITIES.items() if v == val), None)
    if label is None:
        await callback.answer("Невідомий пріоритет.", show_alert=True)
        return
    await state.update_data(priority=val, priority_label=label)
    await state.set_state(TicketForm.photo)
    await callback.message.edit_text(f"Пріоритет: {label}")
    await callback.message.answer("📎 Додайте фото до заявки або пропустіть:", reply_markup=SKIP_PHOTO_MENU)
    await callback.answer()


async def _show_confirm(target: Message, data: dict) -> None:
    photo_label = "✅ додано" if data.get("photo_file_id") else "немає"
    phone_label = data.get("phone") or "не вказано"
    priority_label = data.get("priority_label", "🟡 Середній")
    text = (
        f"Перевірте заявку:\n\n"
        f"📂 Категорія: {hbold(data['category'])}\n"
        f"📝 Опис: {html.escape(data['description'])}\n"
        f"⚡ Пріоритет: {priority_label}\n"
        f"📎 Фото: {photo_label}\n"
        f"📱 Телефон: {phone_label}\n\n"
        "Підтвердити?"
    )
    removal = await target.answer(".", reply_markup=ReplyKeyboardRemove())
    await removal.delete()
    await target.answer(text, reply_markup=confirm_keyboard())


@dp.message(TicketForm.photo, F.photo)
async def process_photo(message: Message, state: FSMContext) -> None:
    photo = message.photo[-1]
    await state.update_data(
        photo_file_id=photo.file_id,
        photo_filename=f"photo_{photo.file_id[:8]}.jpg",
    )
    await state.set_state(TicketForm.phone)
    await message.answer("📱 Вкажіть номер для зворотного зв'язку:", reply_markup=PHONE_MENU)


@dp.message(TicketForm.photo, F.text == "⏭ Пропустити фото")
async def skip_photo(message: Message, state: FSMContext) -> None:
    await state.set_state(TicketForm.phone)
    await message.answer("📱 Вкажіть номер для зворотного зв'язку:", reply_markup=PHONE_MENU)


@dp.message(TicketForm.photo)
async def photo_invalid(message: Message) -> None:
    await message.answer("📷 Надішліть фото або натисніть '⏭ Пропустити фото'.")


@dp.message(TicketForm.phone, F.contact)
async def process_phone(message: Message, state: FSMContext) -> None:
    await state.update_data(phone=message.contact.phone_number)
    await state.set_state(TicketForm.confirm)
    data = await state.get_data()
    await _show_confirm(message, data)


@dp.message(TicketForm.phone, F.text == "⏭ Пропустити")
async def skip_phone(message: Message, state: FSMContext) -> None:
    await state.set_state(TicketForm.confirm)
    data = await state.get_data()
    await _show_confirm(message, data)


@dp.message(TicketForm.phone)
async def phone_invalid(message: Message) -> None:
    await message.answer("📱 Натисніть '📱 Поділитися номером' або '⏭ Пропустити'.")


@dp.callback_query(TicketForm.confirm, F.data == "confirm:yes")
async def process_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()
    await state.clear()

    category_name: str = data["category"]
    description: str = data["description"]
    category_id: int = CATEGORIES[category_name]

    await callback.message.edit_text("⏳ Створюю заявку...")
    await callback.answer()

    try:
        result = await glpi.create_ticket(
            name=description[:80],
            content=description,
            category_id=category_id,
            telegram_user_id=callback.from_user.id,
            phone=data.get("phone"),
            priority=data.get("priority", 3),
        )
        ticket_id = result.get("id")
        if not ticket_id:
            raise ValueError(f"GLPI не повернув ID тікету: {result}")
    except Exception as e:
        log.error("Помилка створення заявки: %s", e)
        await callback.message.edit_text(
            "❌ Не вдалося створити заявку. Спробуйте пізніше або зверніться до адміністратора."
        )
        await bot.send_message(callback.from_user.id, "Головне меню:", reply_markup=MAIN_MENU)
        return

    if data.get("photo_file_id"):
        try:
            file_io = await bot.download(data["photo_file_id"])
            doc_id = await glpi.upload_document(file_io.read(), data["photo_filename"])
            await glpi.link_document_to_ticket(doc_id, ticket_id)
        except Exception as e:
            log.error("Не вдалося прикріпити фото до заявки #%s: %s", ticket_id, e)

    ticket_url = f"{GLPI_EXTERNAL_URL}/front/ticket.form.php?id={ticket_id}"
    user = callback.from_user

    await callback.message.edit_text(
        f"✅ Заявку {hbold(f'#{ticket_id}')} створено!\n"
        f"Ми зв'яжемося з вами найближчим часом."
    )
    await bot.send_message(callback.from_user.id, "Головне меню:", reply_markup=MAIN_MENU)

    username_part = f"@{user.username}" if user.username else user.full_name
    phone_part = f"\n📱 Тел: {data['phone']}" if data.get("phone") else ""
    priority_label = data.get("priority_label", "🟡 Середній")
    await bot.send_message(
        TECHNICIANS_CHAT_ID,
        f"🆕 Нова заявка {hbold(f'#{ticket_id}')}\n"
        f"👤 Від: {username_part} (ID: {hcode(str(user.id))}){phone_part}\n"
        f"📂 Категорія: {html.escape(category_name)}\n"
        f"⚡ Пріоритет: {priority_label}\n"
        f"📝 {html.escape(description)}\n"
        f"🔗 {ticket_url}",
    )


@dp.callback_query(TicketForm.confirm, F.data == "confirm:no")
async def process_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Заявку скасовано.")
    await callback.answer()
    await bot.send_message(callback.from_user.id, "Головне меню:", reply_markup=MAIN_MENU)


# ---------------------------------------------------------------------------
# Список заявок + пагінація
# ---------------------------------------------------------------------------


def _build_tickets_message(tickets: list[dict]) -> str:
    lines = ["📋 <b>Ваші заявки:</b>\n"]
    for ticket in tickets:
        ticket_id = ticket.get(GLPI_FIELD_ID, "?")
        name = ticket.get(GLPI_FIELD_NAME, "—")
        status_id = int(ticket.get(GLPI_FIELD_STATUS, 0))
        date_raw = ticket.get(GLPI_FIELD_DATE, "")
        status_label = TICKET_STATUSES.get(status_id, f"#{status_id}")
        date_label = date_raw[:10] if date_raw else "—"
        lines.append(f"<b>#{ticket_id}</b> — {html.escape(str(name))}\n{status_label} | {date_label}\n")
    return "\n".join(lines)


@dp.message(F.text == "📋 Мої заявки")
async def my_tickets(message: Message) -> None:
    wait = await message.answer("⏳ Завантажую ваші заявки...")
    try:
        tickets, total = await glpi.get_user_tickets(message.from_user.id, offset=0)
    except Exception as e:
        log.error("Помилка отримання заявок: %s", e)
        await wait.edit_text("❌ Не вдалося отримати заявки. Спробуйте пізніше.")
        return

    if not tickets:
        await wait.edit_text("У вас ще немає заявок.")
        return

    text = _build_tickets_message(tickets)
    kb = tickets_keyboard(tickets, offset=0, total=total)
    await wait.edit_text(text, reply_markup=kb)


@dp.callback_query(F.data.startswith("mytickets:"))
async def tickets_page(callback: CallbackQuery) -> None:
    try:
        offset = int(callback.data.removeprefix("mytickets:"))
    except ValueError:
        await callback.answer()
        return
    try:
        tickets, total = await glpi.get_user_tickets(callback.from_user.id, offset=offset)
    except Exception as e:
        log.error("Помилка пагінації заявок: %s", e)
        await callback.answer("❌ Не вдалося завантажити заявки.", show_alert=True)
        return
    if not tickets:
        await callback.message.edit_text("У вас ще немає заявок.")
        await callback.answer()
        return
    text = _build_tickets_message(tickets)
    kb = tickets_keyboard(tickets, offset=offset, total=total)
    await callback.message.edit_text(text, reply_markup=kb)
    await callback.answer()


@dp.callback_query(F.data == "noop")
async def noop_callback(callback: CallbackQuery) -> None:
    await callback.answer()


# ---------------------------------------------------------------------------
# Деталі заявки
# ---------------------------------------------------------------------------


@dp.callback_query(F.data.startswith("tdetail:"))
async def ticket_detail(callback: CallbackQuery) -> None:
    try:
        ticket_id = int(callback.data.removeprefix("tdetail:"))
    except ValueError:
        await callback.answer()
        return
    try:
        ticket = await glpi.get_ticket(ticket_id)
        followups = await glpi.get_ticket_followups(ticket_id)
    except Exception as e:
        log.error("Помилка отримання деталей заявки #%s: %s", ticket_id, e)
        await callback.answer("❌ Не вдалося завантажити деталі.", show_alert=True)
        return

    status_id = ticket.get("status", 0)
    status_label = TICKET_STATUSES.get(status_id, f"#{status_id}")
    date_open = (ticket.get("date") or ticket.get("date_creation") or "")[:10]
    date_mod = (ticket.get("date_mod") or "")[:10]
    content = html.escape(str(ticket.get("content") or "—"))

    lines = [
        f"📋 <b>Заявка #{ticket_id}</b>\n",
        f"📝 {html.escape(str(ticket.get('name', '—')))}",
        f"{status_label} | відкрито: {date_open} | змінено: {date_mod}",
        f"\n<b>Опис:</b>\n{content[:500]}",
    ]

    if followups:
        lines.append("\n<b>Коментарі техніка:</b>")
        for fu in followups[-3:]:  # останні 3
            fu_content = html.escape(str(fu.get("content") or ""))[:300]
            fu_date = (fu.get("date_creation") or "")[:10]
            lines.append(f"[{fu_date}] {fu_content}")

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="↩ До списку", callback_data="mytickets:0")
    ]])
    await callback.message.edit_text("\n".join(lines), reply_markup=kb)
    await callback.answer()


# ---------------------------------------------------------------------------
# Скасування заявки
# ---------------------------------------------------------------------------


@dp.callback_query(F.data.startswith("cancel:"))
async def cancel_ticket_callback(callback: CallbackQuery) -> None:
    try:
        ticket_id = int(callback.data.removeprefix("cancel:"))
    except ValueError:
        await callback.answer()
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Так, скасувати", callback_data=f"cancel_yes:{ticket_id}"),
        InlineKeyboardButton(text="↩ Назад", callback_data="mytickets:0"),
    ]])
    await callback.message.edit_text(
        f"Скасувати заявку <b>#{ticket_id}</b>? Цю дію не можна відмінити.",
        reply_markup=kb,
    )
    await callback.answer()


@dp.callback_query(F.data.startswith("cancel_yes:"))
async def cancel_ticket_confirm(callback: CallbackQuery) -> None:
    try:
        ticket_id = int(callback.data.removeprefix("cancel_yes:"))
    except ValueError:
        await callback.answer()
        return

    # Перевірка власника: заявка має належати поточному користувачу
    try:
        user_tickets, _ = await glpi.get_user_tickets(callback.from_user.id, offset=0, limit=20)
    except Exception as e:
        log.error("Помилка перевірки заявок при скасуванні: %s", e)
        await callback.answer("❌ Не вдалося перевірити заявку.", show_alert=True)
        return

    owned_ids = {t.get(GLPI_FIELD_ID) for t in user_tickets}
    if str(ticket_id) not in owned_ids:
        await callback.answer("⛔ Ця заявка вам не належить.", show_alert=True)
        return

    try:
        await glpi.cancel_ticket(ticket_id)
        await callback.answer(f"Заявку #{ticket_id} скасовано.", show_alert=True)
    except Exception as e:
        log.error("Помилка скасування заявки #%s: %s", ticket_id, e)
        await callback.answer("❌ Не вдалося скасувати заявку.", show_alert=True)
        return

    try:
        tickets, total = await glpi.get_user_tickets(callback.from_user.id, offset=0)
        text = _build_tickets_message(tickets)
        kb = tickets_keyboard(tickets, offset=0, total=total)
        await callback.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Follow-up: відповідь користувача на коментар техніка
# ---------------------------------------------------------------------------


@dp.callback_query(F.data.startswith("fu_reply:"))
async def followup_reply_start(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        ticket_id = int(callback.data.removeprefix("fu_reply:"))
    except ValueError:
        await callback.answer()
        return
    await state.set_state(FollowupForm.reply)
    await state.update_data(ticket_id=ticket_id)
    await callback.message.answer(
        f"✏️ Введіть відповідь до заявки <b>#{ticket_id}</b>:",
        reply_markup=ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="❌ Скасувати заявку")]],
            resize_keyboard=True,
        ),
    )
    await callback.answer()


@dp.message(FollowupForm.reply, F.text)
async def followup_reply_send(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    ticket_id = data["ticket_id"]
    try:
        await glpi.add_followup(ticket_id, message.text)
        await state.clear()
        await message.answer(
            f"✅ Відповідь до заявки #{ticket_id} надіслано.", reply_markup=MAIN_MENU
        )
    except Exception as e:
        log.error("Помилка надсилання follow-up до #%s: %s", ticket_id, e)
        await message.answer("❌ Не вдалося надіслати відповідь. Спробуйте ще раз.")


@dp.message(FollowupForm.reply)
async def followup_reply_invalid(message: Message) -> None:
    await message.answer("✏️ Введіть текстову відповідь.")


# ---------------------------------------------------------------------------
# Глобальний error handler
# ---------------------------------------------------------------------------


@dp.error()
async def global_error_handler(event: ErrorEvent) -> None:
    log.exception("Необроблений виняток", exc_info=event.exception)
    if ADMIN_CHAT_ID:
        exc = event.exception
        try:
            await bot.send_message(
                ADMIN_CHAT_ID,
                f"❌ <b>Помилка бота</b>\n"
                f"{html.escape(type(exc).__name__)}: {html.escape(str(exc)[:400])}",
            )
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Polling оновлень заявок
# ---------------------------------------------------------------------------

_notified_statuses: dict[int, int] = {}   # {ticket_id: last_notified_status}
_notified_followups: set[int] = set()     # follow-up IDs


async def _notify_status_changes(since: datetime) -> None:
    for status in (2, 5, 6):
        try:
            tickets = await glpi.get_tickets_changed_since(since, status)
        except Exception as e:
            log.error("Помилка отримання змін статусу %s: %s", status, e)
            continue

        for ticket in tickets:
            ticket_id_raw = ticket.get(GLPI_FIELD_ID)
            try:
                ticket_id = int(ticket_id_raw)
            except (TypeError, ValueError):
                continue

            if _notified_statuses.get(ticket_id) == status:
                continue  # вже сповіщали про цей статус

            content = ticket.get(GLPI_FIELD_CONTENT, "")
            match = re.search(r'\[tg:(\d+)\]', content)
            if not match:
                continue
            telegram_user_id = int(match.group(1))
            ticket_name = ticket.get(GLPI_FIELD_NAME, "—")
            bold_ticket = hbold(f"#{ticket_id} «{ticket_name}»")
            msg = STATUS_NOTIFY_MESSAGES[status].format(ticket=bold_ticket)
            try:
                await bot.send_message(telegram_user_id, msg)
                _notified_statuses[ticket_id] = status
                log.info("Сповіщено %s про статус %s заявки #%s", telegram_user_id, status, ticket_id)
            except Exception as e:
                log.warning("Не вдалося сповістити %s: %s", telegram_user_id, e)


async def _notify_new_followups(since: datetime) -> None:
    try:
        followups = await glpi.get_recent_followups(since)
    except Exception as e:
        log.error("Помилка отримання follow-ups: %s", e)
        return

    for fu in followups:
        fu_id_raw = fu.get("2")
        try:
            fu_id = int(fu_id_raw)
        except (TypeError, ValueError):
            continue

        if fu_id in _notified_followups:
            continue

        ticket_id_raw = fu.get("3")
        fu_content = str(fu.get("4") or "")

        try:
            ticket_id = int(ticket_id_raw)
            ticket = await glpi.get_ticket(ticket_id)
        except Exception as e:
            log.warning("Не вдалося отримати заявку #%s для follow-up: %s", ticket_id_raw, e)
            continue

        ticket_content = ticket.get("content", "")
        match = re.search(r'\[tg:(\d+)\]', ticket_content)
        if not match:
            _notified_followups.add(fu_id)  # не наша заявка — більше не перевіряємо
            continue

        telegram_user_id = int(match.group(1))
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✏️ Відповісти", callback_data=f"fu_reply:{ticket_id}")
        ]])
        try:
            await bot.send_message(
                telegram_user_id,
                f"💬 Новий коментар техніка до заявки {hbold(f'#{ticket_id}')}:\n\n"
                f"{html.escape(fu_content[:800])}",
                reply_markup=kb,
            )
            _notified_followups.add(fu_id)
            log.info("Сповіщено %s про follow-up #%s заявки #%s", telegram_user_id, fu_id, ticket_id)
        except Exception as e:
            log.warning("Не вдалося сповістити %s про follow-up: %s", telegram_user_id, e)


async def check_ticket_updates() -> None:
    last_check = datetime.now(timezone.utc)
    while True:
        await asyncio.sleep(POLL_INTERVAL_SEC)
        now = datetime.now(timezone.utc)
        try:
            await _notify_status_changes(last_check)
            await _notify_new_followups(last_check)
        except Exception as e:
            log.error("Помилка перевірки оновлень заявок: %s", e)
        last_check = now


# ---------------------------------------------------------------------------
# Точка входу
# ---------------------------------------------------------------------------


async def main() -> None:
    try:
        await glpi.init_session()
        log.info("GLPI сесію успішно ініціалізовано")
        CATEGORIES.update(await glpi.get_categories())
        log.info("Завантажено категорій: %d → %s", len(CATEGORIES), list(CATEGORIES.keys()))
    except Exception as e:
        log.warning("Не вдалося підключитися до GLPI при старті: %s. Буде повторна спроба при першому запиті.", e)

    _background_tasks: set = set()
    task = asyncio.create_task(check_ticket_updates())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await glpi.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
