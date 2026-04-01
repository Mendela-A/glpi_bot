import html
import logging

from aiogram import Bot, F, Router
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
)
from aiogram.utils.markdown import hbold, hcode

from config import CATEGORIES, GLPI_EXTERNAL_URL, PRIORITIES, TECHNICIANS_CHAT_ID
from keyboards import (
    MAIN_MENU,
    PHONE_MENU,
    SKIP_PHOTO_MENU,
    categories_keyboard,
    confirm_keyboard,
    priority_keyboard,
)
from polling import _bot_tickets, _ticket_status_cache
from services import bot, glpi
from states import TicketForm

log = logging.getLogger(__name__)
router = Router()


@router.message(F.text == "❌ Скасувати заявку", StateFilter(TicketForm))
async def cancel_form_reply(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Заявку скасовано.", reply_markup=MAIN_MENU)


@router.callback_query(F.data == "form:cancel", StateFilter(TicketForm))
async def cancel_form_inline(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Заявку скасовано.")
    await callback.message.answer("Головне меню:", reply_markup=MAIN_MENU)
    await callback.answer()


@router.message(F.text == "📝 Створити заявку")
async def start_ticket(message: Message, state: FSMContext) -> None:
    kb = categories_keyboard()
    if kb is None:
        await message.answer("⚠️ Категорії недоступні. Спробуйте пізніше.")
        return
    await state.set_state(TicketForm.category)
    removal = await message.answer(".", reply_markup=ReplyKeyboardRemove())
    await removal.delete()
    await message.answer("Оберіть категорію заявки:", reply_markup=kb)


@router.callback_query(TicketForm.category, F.data.startswith("cat:"))
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


@router.message(TicketForm.description, F.text)
async def process_description(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    if len(text) < 5:
        await message.answer("Будь ласка, опишіть проблему детальніше (мінімум 5 символів).")
        return
    if len(text) > 2000:
        await message.answer("Опис занадто довгий (максимум 2000 символів). Будь ласка, скоротіть.")
        return
    await state.update_data(description=text)
    await state.set_state(TicketForm.priority)
    await message.answer("Оберіть пріоритет заявки:", reply_markup=priority_keyboard())


@router.message(TicketForm.description)
async def description_invalid(message: Message) -> None:
    await message.answer("✏️ Введіть текстовий опис проблеми.")


@router.callback_query(TicketForm.priority, F.data.startswith("pri:"))
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


@router.message(TicketForm.photo, F.photo)
async def process_photo(message: Message, state: FSMContext) -> None:
    photo = message.photo[-1]
    await state.update_data(
        photo_file_id=photo.file_id,
        photo_filename=f"photo_{photo.file_id[:8]}.jpg",
    )
    await state.set_state(TicketForm.phone)
    await message.answer("📱 Вкажіть номер для зворотного зв'язку:", reply_markup=PHONE_MENU)


@router.message(TicketForm.photo, F.text == "⏭ Пропустити фото")
async def skip_photo(message: Message, state: FSMContext) -> None:
    await state.set_state(TicketForm.phone)
    await message.answer("📱 Вкажіть номер для зворотного зв'язку:", reply_markup=PHONE_MENU)


@router.message(TicketForm.photo)
async def photo_invalid(message: Message) -> None:
    await message.answer("📷 Надішліть фото або натисніть '⏭ Пропустити фото'.")


@router.message(TicketForm.phone, F.contact)
async def process_phone(message: Message, state: FSMContext) -> None:
    await state.update_data(phone=message.contact.phone_number)
    await state.set_state(TicketForm.confirm)
    data = await state.get_data()
    await _show_confirm(message, data)


@router.message(TicketForm.phone, F.text == "⏭ Пропустити")
async def skip_phone(message: Message, state: FSMContext) -> None:
    await state.set_state(TicketForm.confirm)
    data = await state.get_data()
    await _show_confirm(message, data)


@router.message(TicketForm.phone)
async def phone_invalid(message: Message) -> None:
    await message.answer("📱 Натисніть '📱 Поділитися номером' або '⏭ Пропустити'.")


@router.callback_query(TicketForm.confirm, F.data == "confirm:yes")
async def process_confirm(callback: CallbackQuery, state: FSMContext) -> None:
    data = await state.get_data()

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

    await state.clear()

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
    try:
        await bot.send_message(
            TECHNICIANS_CHAT_ID,
            f"🆕 Нова заявка {hbold(f'#{ticket_id}')}\n"
            f"👤 Від: {username_part} (ID: {hcode(str(user.id))}){phone_part}\n"
            f"📂 Категорія: {html.escape(category_name)}\n"
            f"⚡ Пріоритет: {priority_label}\n"
            f"📝 {html.escape(description)}\n"
            f"🔗 {ticket_url}",
        )
    except Exception as e:
        log.warning("Не вдалося сповістити технічний чат: %s", e)

    _bot_tickets[ticket_id] = user.id
    _ticket_status_cache[ticket_id] = 1  # статус "Нова"


@router.callback_query(TicketForm.confirm, F.data == "confirm:no")
async def process_cancel(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.message.edit_text("Заявку скасовано.")
    await callback.answer()
    await bot.send_message(callback.from_user.id, "Головне меню:", reply_markup=MAIN_MENU)
