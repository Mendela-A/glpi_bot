import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, KeyboardButton, Message, ReplyKeyboardMarkup

from keyboards import MAIN_MENU
from services import glpi
from states import FollowupForm


async def _get_all_user_ticket_ids(user_id: int) -> set[int]:
    return await glpi.get_all_user_ticket_ids(user_id)

log = logging.getLogger(__name__)
router = Router()


@router.callback_query(F.data.startswith("fu_reply:"))
async def followup_reply_start(callback: CallbackQuery, state: FSMContext) -> None:
    try:
        ticket_id = int(callback.data.removeprefix("fu_reply:"))
    except ValueError:
        await callback.answer()
        return
    if ticket_id <= 0:
        await callback.answer()
        return

    # Перевірка власника: заявка має належати поточному користувачу
    try:
        owned = await _get_all_user_ticket_ids(callback.from_user.id)
    except Exception as e:
        log.error("Помилка перевірки заявок при reply: %s", e)
        await callback.answer("❌ Не вдалося перевірити заявку.", show_alert=True)
        return
    if ticket_id not in owned:
        await callback.answer("⛔ Ця заявка вам не належить.", show_alert=True)
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


@router.message(FollowupForm.reply, F.text)
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


@router.message(FollowupForm.reply)
async def followup_reply_invalid(message: Message) -> None:
    await message.answer("✏️ Введіть текстову відповідь.")
