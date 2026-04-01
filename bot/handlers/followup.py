import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, KeyboardButton, Message, ReplyKeyboardMarkup

from keyboards import MAIN_MENU
from services import glpi
from states import FollowupForm

log = logging.getLogger(__name__)
router = Router()


@router.callback_query(F.data.startswith("fu_reply:"))
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
