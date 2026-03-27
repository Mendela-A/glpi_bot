import asyncio
import logging
import os
from datetime import datetime, timezone

import json
import aiohttp
from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
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

ALLOWED_USER_IDS: set[int] = {
    int(uid.strip())
    for uid in os.environ.get("ALLOWED_USER_IDS", "").split(",")
    if uid.strip()
}

# Завантажується динамічно з GLPI при старті
CATEGORIES: dict[str, int] = {}

TICKET_STATUS_CLOSED = 6
POLL_INTERVAL_SEC = 300  # 5 хвилин

TICKET_STATUSES: dict[int, str] = {
    1: "🆕 Нова",
    2: "🔧 В роботі",
    3: "🔧 В роботі",
    4: "⏳ Очікує",
    5: "✅ Вирішена",
    6: "🔒 Закрита",
}

# ---------------------------------------------------------------------------
# FSM
# ---------------------------------------------------------------------------


class TicketForm(StatesGroup):
    category = State()
    description = State()
    photo = State()
    phone = State()
    confirm = State()


# ---------------------------------------------------------------------------
# GLPI API клієнт
# ---------------------------------------------------------------------------


class GLPIClient:
    def __init__(self) -> None:
        self._session_token: str | None = None
        self._http: aiohttp.ClientSession | None = None

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

    async def create_ticket(self, name: str, content: str, category_id: int, telegram_user_id: int, phone: str | None = None) -> dict:
        await self._ensure_session()
        http = await self._get_http()
        extra = f"\nТелефон: {phone}" if phone else ""
        tagged_content = f"{content}{extra}\n\n[tg:{telegram_user_id}]"
        payload = {
            "input": {
                "name": name,
                "content": tagged_content,
                "itilcategories_id": category_id,
                "type": 1,           # 1 = Incident
                "urgency": 3,        # Medium
                "impact": 3,
                "requesttypes_id": 7,  # Telegram Bot
            }
        }
        async with http.post(
            f"{GLPI_URL}/apirest.php/Ticket",
            json=payload,
            headers=self._auth_headers,
        ) as resp:
            if resp.status == 401:
                await self.init_session()
                async with http.post(
                    f"{GLPI_URL}/apirest.php/Ticket",
                    json=payload,
                    headers=self._auth_headers,
                ) as retry:
                    retry.raise_for_status()
                    return await retry.json()
            resp.raise_for_status()
            return await resp.json()

    async def get_user_tickets(self, user_id: int) -> list[dict]:
        """Повертає заявки користувача за міткою [tg:user_id] в content."""
        await self._ensure_session()
        http = await self._get_http()
        params = {
            "criteria[0][field]": "21",           # content
            "criteria[0][searchtype]": "contains",
            "criteria[0][value]": f"[tg:{user_id}]",
            "forcedisplay[0]": "2",               # name
            "forcedisplay[1]": "12",              # status
            "forcedisplay[2]": "19",              # date_creation
            "sort": "19",
            "order": "DESC",
            "range": "0-20",
        }
        async with http.get(
            f"{GLPI_URL}/apirest.php/search/Ticket",
            params=params,
            headers=self._auth_headers,
        ) as resp:
            if resp.status == 401:
                await self.init_session()
                return await self.get_user_tickets(user_id)
            if resp.status in (200, 206):
                data = await resp.json()
                return data.get("data", [])
            return []

    async def get_categories(self) -> dict[str, int]:
        """Повертає словник {назва: id} категорій з GLPI."""
        await self._ensure_session()
        http = await self._get_http()
        async with http.get(
            f"{GLPI_URL}/apirest.php/ITILCategory",
            params={"range": "0-200", "is_helpdeskvisible": 1},
            headers=self._auth_headers,
        ) as resp:
            if resp.status == 401:
                await self.init_session()
                return await self.get_categories()
            resp.raise_for_status()
            data = await resp.json()
            return {item["completename"]: item["id"] for item in data if isinstance(item, dict)}

    async def get_recently_closed_tickets(self, since: datetime) -> list[dict]:
        """Повертає заявки зі статусом Closed, закриті після `since`."""
        await self._ensure_session()
        http = await self._get_http()
        params = {
            "criteria[0][field]": "12",          # status field
            "criteria[0][searchtype]": "equals",
            "criteria[0][value]": str(TICKET_STATUS_CLOSED),
            "criteria[1][field]": "17",          # closedate
            "criteria[1][searchtype]": "morethan",
            "criteria[1][value]": since.strftime("%Y-%m-%d %H:%M:%S"),
            "forcedisplay[0]": "2",              # name
            "forcedisplay[1]": "12",             # status
            "forcedisplay[2]": "17",             # closedate
            "range": "0-50",
        }
        async with http.get(
            f"{GLPI_URL}/apirest.php/search/Ticket",
            params=params,
            headers=self._auth_headers,
        ) as resp:
            if resp.status == 401:
                await self.init_session()
                return await self.get_recently_closed_tickets(since)
            if resp.status == 206 or resp.status == 200:
                data = await resp.json()
                return data.get("data", [])
            return []

    async def upload_document(self, file_bytes: bytes, filename: str) -> int:
        """Завантажує файл у GLPI і повертає document id."""
        await self._ensure_session()
        http = await self._get_http()
        manifest = json.dumps({"input": {"name": filename, "_filename": [filename]}})
        form = aiohttp.FormData()
        form.add_field("uploadManifest", manifest, content_type="application/json")
        form.add_field("filename[0]", file_bytes, filename=filename, content_type="image/jpeg")
        headers = {k: v for k, v in self._auth_headers.items() if k != "Content-Type"}
        async with http.post(
            f"{GLPI_URL}/apirest.php/Document",
            data=form,
            headers=headers,
        ) as resp:
            if resp.status == 401:
                await self.init_session()
                return await self.upload_document(file_bytes, filename)
            resp.raise_for_status()
            data = await resp.json()
            return data["id"]

    async def link_document_to_ticket(self, doc_id: int, ticket_id: int) -> None:
        """Прив'язує документ до тікету."""
        await self._ensure_session()
        http = await self._get_http()
        payload = {"input": {"documents_id": doc_id, "items_id": ticket_id, "itemtype": "Ticket"}}
        async with http.post(
            f"{GLPI_URL}/apirest.php/Document_Item",
            json=payload,
            headers=self._auth_headers,
        ) as resp:
            if resp.status == 401:
                await self.init_session()
                await self.link_document_to_ticket(doc_id, ticket_id)
                return
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
    buttons = [
        [InlineKeyboardButton(text=name, callback_data=f"cat:{str(cat_id)}")]
        for name, cat_id in CATEGORIES.items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


PHONE_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📱 Поділитися номером", request_contact=True)],
        [KeyboardButton(text="⏭ Пропустити")],
    ],
    resize_keyboard=True,
)

SKIP_PHOTO_MENU = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="⏭ Пропустити фото")]],
    resize_keyboard=True,
)


def confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Підтвердити", callback_data="confirm:yes"),
                InlineKeyboardButton(text="❌ Скасувати", callback_data="confirm:no"),
            ]
        ]
    )


# ---------------------------------------------------------------------------
# Telegram Bot + Dispatcher
# ---------------------------------------------------------------------------

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(storage=MemoryStorage())
glpi = GLPIClient()


# ---------------------------------------------------------------------------
# Middleware авторизації
# ---------------------------------------------------------------------------


# @dp.message.middleware()
# async def auth_middleware(handler, event: Message, data: dict):
#     if ALLOWED_USER_IDS and event.from_user.id not in ALLOWED_USER_IDS:
#         await event.answer("⛔ У вас немає доступу до цього бота.")
#         return
#     return await handler(event, data)


# @dp.callback_query.middleware()
# async def auth_callback_middleware(handler, event: CallbackQuery, data: dict):
#     if ALLOWED_USER_IDS and event.from_user.id not in ALLOWED_USER_IDS:
#         await event.answer("⛔ Немає доступу.", show_alert=True)
#         return
#     return await handler(event, data)


# ---------------------------------------------------------------------------
# Хендлери
# ---------------------------------------------------------------------------


@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        f"Вітаю, {hbold(message.from_user.full_name)}!\n"
        "Я допоможу вам створити заявку в IT-відділ.",
        reply_markup=MAIN_MENU,
    )


@dp.message(F.text == "📝 Створити заявку")
async def start_ticket(message: Message, state: FSMContext) -> None:
    kb = categories_keyboard()
    if kb is None:
        await message.answer("⚠️ Категорії недоступні. Спробуйте пізніше.")
        return
    await state.set_state(TicketForm.category)
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
        f"Категорія: {hbold(category_name)}\n\nОпишіть вашу проблему:"
    )
    await callback.answer()


@dp.message(TicketForm.description)
async def process_description(message: Message, state: FSMContext) -> None:
    if not message.text or len(message.text.strip()) < 5:
        await message.answer("Будь ласка, опишіть проблему детальніше (мінімум 5 символів).")
        return
    await state.update_data(description=message.text.strip())
    await state.set_state(TicketForm.photo)
    await message.answer(
        "📎 Додайте фото до заявки або пропустіть:",
        reply_markup=SKIP_PHOTO_MENU,
    )


async def _show_confirm(target: Message, data: dict) -> None:
    photo_label = "✅ додано" if data.get("photo_bytes") else "немає"
    phone_label = data.get("phone") or "не вказано"
    text = (
        f"Перевірте заявку:\n\n"
        f"📂 Категорія: {hbold(data['category'])}\n"
        f"📝 Опис: {data['description']}\n"
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
    file_io = await bot.download(photo.file_id)
    await state.update_data(
        photo_bytes=file_io.read(),
        photo_filename=f"photo_{photo.file_id[:8]}.jpg",
    )
    await state.set_state(TicketForm.phone)
    await message.answer("📱 Вкажіть номер для зворотного зв'язку:", reply_markup=PHONE_MENU)


@dp.message(TicketForm.photo, F.text == "⏭ Пропустити фото")
async def skip_photo(message: Message, state: FSMContext) -> None:
    await state.set_state(TicketForm.phone)
    await message.answer("📱 Вкажіть номер для зворотного зв'язку:", reply_markup=PHONE_MENU)


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
            name=f"{category_name}: {description[:60]}",
            content=description,
            category_id=category_id,
            telegram_user_id=callback.from_user.id,
            phone=data.get("phone"),
        )
        ticket_id = result.get("id")
        if not ticket_id:
            raise ValueError(f"GLPI не повернув ID тікету: {result}")
    except Exception as e:
        log.error("Помилка створення заявки: %s", e)
        await callback.message.edit_text(
            "❌ Не вдалося створити заявку. Спробуйте пізніше або зверніться до адміністратора."
        )
        return

    if data.get("photo_bytes"):
        try:
            doc_id = await glpi.upload_document(data["photo_bytes"], data["photo_filename"])
            await glpi.link_document_to_ticket(doc_id, ticket_id)
        except Exception as e:
            log.error("Не вдалося прикріпити фото до заявки #%s: %s", ticket_id, e)

    ticket_url = f"{GLPI_EXTERNAL_URL}/front/ticket.form.php?id={ticket_id}"
    user = callback.from_user

    await callback.message.edit_text(
        f"✅ Заявку {hbold(f'#{ticket_id}')} створено!\n"
        f"Ми зв'яжемося з вами найближчим часом."
    )

    username_part = f"@{user.username}" if user.username else user.full_name
    phone_part = f"\n📱 Тел: {data['phone']}" if data.get("phone") else ""
    await bot.send_message(
        TECHNICIANS_CHAT_ID,
        f"🆕 Нова заявка {hbold(f'#{ticket_id}')}\n"
        f"👤 Від: {username_part} (ID: {hcode(str(user.id))}){phone_part}\n"
        f"📂 Категорія: {category_name}\n"
        f"📝 {description}\n"
        f"🔗 {ticket_url}",
    )


@dp.callback_query(TicketForm.confirm, F.data == "confirm:no")
async def process_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Заявку скасовано.")
    await callback.answer()
    await bot.send_message(callback.from_user.id, "Головне меню:", reply_markup=MAIN_MENU)


@dp.message(F.text == "📋 Мої заявки")
async def my_tickets(message: Message) -> None:
    wait = await message.answer("⏳ Завантажую ваші заявки...")
    try:
        tickets = await glpi.get_user_tickets(message.from_user.id)
    except Exception as e:
        log.error("Помилка отримання заявок: %s", e)
        await wait.edit_text("❌ Не вдалося отримати заявки. Спробуйте пізніше.")
        return

    if not tickets:
        await wait.edit_text("У вас ще немає заявок.")
        return

    lines = ["📋 <b>Ваші заявки:</b>\n"]
    for ticket in tickets:
        ticket_id = ticket.get("2", "?")
        name = ticket.get("1", "—")
        status_id = int(ticket.get("12", 0))
        date_raw = ticket.get("19", "")
        status_label = TICKET_STATUSES.get(status_id, f"#{status_id}")
        date_label = date_raw[:10] if date_raw else "—"
        lines.append(f"<b>#{ticket_id}</b> — {name}\n{status_label} | {date_label}\n")

    await wait.edit_text("\n".join(lines))


# ---------------------------------------------------------------------------
# Polling закритих заявок
# ---------------------------------------------------------------------------


async def check_closed_tickets() -> None:
    last_check = datetime.now(timezone.utc)
    while True:
        await asyncio.sleep(POLL_INTERVAL_SEC)
        now = datetime.now(timezone.utc)
        try:
            tickets = await glpi.get_recently_closed_tickets(since=last_check)
            for ticket in tickets:
                ticket_id = ticket.get("2") or ticket.get("id", "?")
                ticket_name = ticket.get("1") or ticket.get("name", "—")
                await bot.send_message(
                    TECHNICIANS_CHAT_ID,
                    f"✅ Заявку {hbold(f'#{ticket_id}')} закрито\n"
                    f"📂 {ticket_name}",
                )
        except Exception as e:
            log.error("Помилка перевірки закритих заявок: %s", e)
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

    asyncio.create_task(check_closed_tickets())
    try:
        await dp.start_polling(bot)
    finally:
        await glpi.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
