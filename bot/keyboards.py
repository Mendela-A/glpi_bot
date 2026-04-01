import math

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from config import CATEGORIES, GLPI_FIELD_ID, GLPI_FIELD_STATUS, PRIORITIES, TICKETS_PER_PAGE

MAIN_MENU = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📝 Створити заявку")],
        [KeyboardButton(text="📋 Мої заявки")],
    ],
    resize_keyboard=True,
)

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


def categories_keyboard() -> InlineKeyboardMarkup | None:
    if not CATEGORIES:
        return None
    items = [
        InlineKeyboardButton(text=name, callback_data=f"cat:{cat_id}")
        for name, cat_id in CATEGORIES.items()
    ]
    buttons = [items[i:i + 2] for i in range(0, len(items), 2)]
    buttons.append([InlineKeyboardButton(text="❌ Скасувати", callback_data="form:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


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
                text="🗑 Скасувати",
                callback_data=f"cancel:{ticket_id}",
            ))
        rows.append(row)

    total_pages = math.ceil(total / TICKETS_PER_PAGE) or 1
    current_page = offset // TICKETS_PER_PAGE + 1
    nav = []
    if offset > 0:
        nav.append(InlineKeyboardButton(text="← Назад", callback_data=f"mytickets:{offset - TICKETS_PER_PAGE}"))
    nav.append(InlineKeyboardButton(text=f"{current_page}/{total_pages}", callback_data="noop"))
    if offset + TICKETS_PER_PAGE < total:
        nav.append(InlineKeyboardButton(text="Далі →", callback_data=f"mytickets:{offset + TICKETS_PER_PAGE}"))
    if len(nav) > 1:
        rows.append(nav)

    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
