import html
import logging

from aiogram import F, Router
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import GLPI_FIELD_DATE, GLPI_FIELD_ID, GLPI_FIELD_NAME, GLPI_FIELD_STATUS, TICKET_STATUSES
from utils import strip_html
from keyboards import MAIN_MENU, tickets_keyboard
from services import glpi

log = logging.getLogger(__name__)
router = Router()


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


@router.message(F.text == "📋 Мої заявки")
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


@router.callback_query(F.data.startswith("mytickets:"))
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


@router.callback_query(F.data == "noop")
async def noop_callback(callback: CallbackQuery) -> None:
    await callback.answer()


@router.callback_query(F.data.startswith("tdetail:"))
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
            fu_content = html.escape(strip_html(str(fu.get("content") or "")))[:300]
            fu_date = (fu.get("date_creation") or "")[:10]
            lines.append(f"[{fu_date}] {fu_content}")

    kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="↩ До списку", callback_data="mytickets:0")
    ]])
    await callback.message.edit_text("\n".join(lines), reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("cancel:"))
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


@router.callback_query(F.data.startswith("cancel_yes:"))
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
