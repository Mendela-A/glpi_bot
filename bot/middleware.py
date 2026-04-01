import time
import logging

from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from config import ALLOWED_GROUP_ID
from services import bot

log = logging.getLogger(__name__)

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
# Перевірка членства в групі
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


# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

async def message_middleware(handler, event: Message, data: dict):
    if event.chat.type != "private":
        return
    if not await is_group_member(event.from_user.id):
        await event.answer("⛔ Доступ тільки для членів групи.")
        return
    # Не застосовуємо rate limit під час активного FSM (заповнення форми)
    state: FSMContext = data.get("state")
    current_state = await state.get_state() if state else None
    if current_state is None and _is_rate_limited(event.from_user.id):
        await event.answer("⚠️ Занадто багато запитів. Зачекайте хвилину.")
        return
    return await handler(event, data)


async def callback_middleware(handler, event: CallbackQuery, data: dict):
    if not await is_group_member(event.from_user.id):
        await event.answer("⛔ Доступ тільки для членів групи.", show_alert=True)
        return
    # Не застосовуємо rate limit під час активного FSM (вибір категорії, пріоритету тощо)
    state: FSMContext = data.get("state")
    current_state = await state.get_state() if state else None
    if current_state is None and _is_rate_limited(event.from_user.id):
        await event.answer("⚠️ Занадто багато запитів.", show_alert=True)
        return
    return await handler(event, data)
