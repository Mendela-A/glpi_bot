import asyncio
import html
import logging
import re

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.markdown import hbold

from config import (
    GLPI_FIELD_CONTENT,
    GLPI_FIELD_ID,
    GLPI_FIELD_STATUS,
    POLL_INTERVAL_SEC,
    STATUS_NOTIFY_MESSAGES,
    TICKET_STATUS_CLOSED,
)
from services import bot, glpi
from utils import strip_html

# Максимальний розмір кешу сповіщених follow-up IDs
_MAX_NOTIFIED_FOLLOWUPS = 10_000

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Кеш активних тікетів, створених через бот
# ---------------------------------------------------------------------------

# {ticket_id: telegram_user_id}
_bot_tickets: dict[int, int] = {}
# {ticket_id: last_known_status}
_ticket_status_cache: dict[int, int] = {}
# follow-up IDs, про які вже сповіщали
_notified_followups: set[int] = set()


async def _load_bot_tickets() -> None:
    """Завантажує з GLPI всі активні тікети, створені через бот."""
    tickets, _ = await glpi.get_active_bot_tickets()
    for ticket in tickets:
        try:
            ticket_id = int(ticket.get(GLPI_FIELD_ID))
        except (TypeError, ValueError):
            continue
        content = ticket.get(GLPI_FIELD_CONTENT, "")
        match = re.search(r'\[tg:(\d+)\]', content)
        if not match:
            continue
        user_id = int(match.group(1))
        status = int(ticket.get(GLPI_FIELD_STATUS, 1))
        _bot_tickets[ticket_id] = user_id
        _ticket_status_cache[ticket_id] = status
    log.info("Завантажено bot-тікетів: %d", len(_bot_tickets))


async def _notify_status_changes() -> None:
    for ticket_id, user_id in list(_bot_tickets.items()):
        try:
            ticket = await glpi.get_ticket(ticket_id)
        except Exception as e:
            log.warning("Не вдалося отримати тікет #%s: %s", ticket_id, e)
            continue

        status = ticket.get("status", 0)
        if status == _ticket_status_cache.get(ticket_id):
            continue  # без змін

        _ticket_status_cache[ticket_id] = status

        if status in STATUS_NOTIFY_MESSAGES:
            ticket_name = ticket.get("name", "—")
            bold = hbold(f"#{ticket_id} «{ticket_name}»")
            msg = STATUS_NOTIFY_MESSAGES[status].format(ticket=bold)
            try:
                await bot.send_message(user_id, msg)
                log.info("Сповіщено %s про статус %s заявки #%s", user_id, status, ticket_id)
            except Exception as e:
                log.warning("Не вдалося сповістити %s: %s", user_id, e)

        if status == TICKET_STATUS_CLOSED:
            _bot_tickets.pop(ticket_id, None)  # закриті більше не відстежуємо
            _ticket_status_cache.pop(ticket_id, None)


async def _notify_new_followups() -> None:
    for ticket_id, user_id in list(_bot_tickets.items()):
        try:
            followups = await glpi.get_ticket_followups(ticket_id)
        except Exception as e:
            log.warning("Не вдалося отримати follow-ups #%s: %s", ticket_id, e)
            continue

        for fu in followups:
            fu_id = fu.get("id")
            if not fu_id or fu_id in _notified_followups:
                continue
            # Запобігаємо безкінечному росту множини
            if len(_notified_followups) >= _MAX_NOTIFIED_FOLLOWUPS:
                _notified_followups.clear()
            _notified_followups.add(fu_id)
            fu_content = strip_html(str(fu.get("content") or ""))
            if not fu_content:
                continue
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(text="✏️ Відповісти", callback_data=f"fu_reply:{ticket_id}")
            ]])
            try:
                await bot.send_message(
                    user_id,
                    f"💬 Коментар техніка до заявки {hbold(f'#{ticket_id}')}:\n\n"
                    f"{html.escape(fu_content[:800])}",
                    reply_markup=kb,
                )
                log.info("Follow-up #%s → user %s заявка #%s", fu_id, user_id, ticket_id)
            except Exception as e:
                log.warning("Не вдалося сповістити %s про follow-up: %s", user_id, e)


async def check_ticket_updates() -> None:
    while True:
        await asyncio.sleep(POLL_INTERVAL_SEC)
        try:
            await _notify_new_followups()   # спочатку коментарі (тікет ще в кеші)
            await _notify_status_changes()  # потім статус (може видалити закриті з кешу)
        except Exception as e:
            log.error("Помилка перевірки оновлень заявок: %s", e)
