import html
import logging

from aiogram import Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import ErrorEvent, Message
from aiogram.utils.markdown import hbold

from config import ADMIN_CHAT_ID
from keyboards import MAIN_MENU
from services import bot
from states import FollowupForm, TicketForm

log = logging.getLogger(__name__)
router = Router()



@router.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer(
        f"Вітаю, {hbold(message.from_user.full_name)}!\n"
        "Я допоможу вам створити заявку в IT-відділ.",
        reply_markup=MAIN_MENU,
    )


@router.message(Command("cancel"), StateFilter(TicketForm, FollowupForm))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Скасовано.", reply_markup=MAIN_MENU)


@router.message(Command("cancel"))
async def cmd_cancel_noop(message: Message) -> None:
    await message.answer("Немає активної дії для скасування.", reply_markup=MAIN_MENU)


@router.message()
async def fallback_handler(message: Message) -> None:
    """Будь-яке повідомлення поза FSM → показати головне меню."""
    await message.answer("Оберіть дію:", reply_markup=MAIN_MENU)


@router.error()
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
