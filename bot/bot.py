import asyncio
import logging
import os
from datetime import datetime, timezone

import aiohttp
from aiogram import Bot, Dispatcher, F
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
GLPI_APP_TOKEN: str = os.environ["GLPI_APP_TOKEN"]
GLPI_USER_TOKEN: str = os.environ["GLPI_USER_TOKEN"]

ALLOWED_USER_IDS: set[int] = {
    int(uid.strip())
    for uid in os.environ.get("ALLOWED_USER_IDS", "").split(",")
    if uid.strip()
}

CATEGORIES: dict[str, int] = {
    "💻 Комп'ютер / ноутбук": 1,
    "🖨 Принтер": 2,
    "🌐 Мережа / інтернет": 3,
    "📦 Програмне забезпечення": 4,
    "🔧 Інше": 5,
}

TICKET_STATUS_CLOSED = 6
POLL_INTERVAL_SEC = 300  # 5 хвилин

# ---------------------------------------------------------------------------
# FSM
# ---------------------------------------------------------------------------


class TicketForm(StatesGroup):
    category = State()
    description = State()
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

    async def create_ticket(self, name: str, content: str, category_id: int) -> dict:
        await self._ensure_session()
        http = await self._get_http()
        payload = {
            "input": {
                "name": name,
                "content": content,
                "itilcategories_id": category_id,
                "type": 1,      # 1 = Incident
                "urgency": 3,   # Medium
                "impact": 3,
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

    async def close(self) -> None:
        await self.kill_session()
        if self._http and not self._http.closed:
            await self._http.close()


# ---------------------------------------------------------------------------
# Клавіатури
# ---------------------------------------------------------------------------

MAIN_MENU = ReplyKeyboardMarkup(
    keyboard=[[KeyboardButton(text="📝 Створити заявку")]],
    resize_keyboard=True,
)


def categories_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=name, callback_data=f"cat:{name}")]
        for name in CATEGORIES
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


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

bot = Bot(token=BOT_TOKEN, parse_mode="HTML")
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
    await state.set_state(TicketForm.category)
    await message.answer(
        "Оберіть категорію заявки:",
        reply_markup=categories_keyboard(),
    )


@dp.callback_query(TicketForm.category, F.data.startswith("cat:"))
async def process_category(callback: CallbackQuery, state: FSMContext) -> None:
    category_name = callback.data.removeprefix("cat:")
    if category_name not in CATEGORIES:
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
    data = await state.get_data()
    await state.set_state(TicketForm.confirm)
    await message.answer(
        f"Перевірте заявку:\n\n"
        f"📂 Категорія: {hbold(data['category'])}\n"
        f"📝 Опис: {data['description']}\n\n"
        "Підтвердити?",
        reply_markup=confirm_keyboard(),
    )


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
        )
        ticket_id = result.get("id") or result[0].get("id")
    except Exception as e:
        log.error("Помилка створення заявки: %s", e)
        await callback.message.edit_text(
            "❌ Не вдалося створити заявку. Спробуйте пізніше або зверніться до адміністратора."
        )
        return

    ticket_url = f"{GLPI_URL}/front/ticket.form.php?id={ticket_id}"
    user = callback.from_user

    await callback.message.edit_text(
        f"✅ Заявку {hbold(f'#{ticket_id}')} створено!\n"
        f"Ми зв'яжемося з вами найближчим часом."
    )

    username_part = f"@{user.username}" if user.username else user.full_name
    await bot.send_message(
        TECHNICIANS_CHAT_ID,
        f"🆕 Нова заявка {hbold(f'#{ticket_id}')}\n"
        f"👤 Від: {username_part} (ID: {hcode(str(user.id))})\n"
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
    await glpi.init_session()
    asyncio.create_task(check_closed_tickets())
    try:
        await dp.start_polling(bot)
    finally:
        await glpi.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
