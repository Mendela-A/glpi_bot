import os
import sys

# Додаємо bot/ до шляху, щоб імпортувати модулі без пакету
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Мінімальні env vars для config.py (не звертається до мережі)
os.environ.setdefault("BOT_TOKEN", "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ123456789")
os.environ.setdefault("TECHNICIANS_CHAT_ID", "100")
os.environ.setdefault("GLPI_URL", "http://localhost")
os.environ.setdefault("GLPI_APP_TOKEN", "app")
os.environ.setdefault("GLPI_USER_TOKEN", "usr")

import pytest
from keyboards import tickets_keyboard
from config import GLPI_FIELD_ID, GLPI_FIELD_STATUS, TICKETS_PER_PAGE


def make_ticket(ticket_id: int, status: int) -> dict:
    return {GLPI_FIELD_ID: str(ticket_id), GLPI_FIELD_STATUS: str(status)}


# ---------------------------------------------------------------------------
# tickets_keyboard — базові перевірки
# ---------------------------------------------------------------------------

class TestTicketsKeyboard:
    def test_empty_list_returns_none(self):
        assert tickets_keyboard([], offset=0, total=0) is None

    def test_single_page_no_nav(self):
        tickets = [make_ticket(1, 1)]
        kb = tickets_keyboard(tickets, offset=0, total=1)
        flat = [btn for row in kb.inline_keyboard for btn in row]
        nav_texts = {"← Назад", "Далі →"}
        assert not any(b.text in nav_texts for b in flat), "Навігація не має з'являтись на одній сторінці"

    def test_prev_button_on_second_page(self):
        tickets = [make_ticket(i, 1) for i in range(1, TICKETS_PER_PAGE + 1)]
        kb = tickets_keyboard(tickets, offset=TICKETS_PER_PAGE, total=TICKETS_PER_PAGE * 2)
        flat = [btn for row in kb.inline_keyboard for btn in row]
        assert any(b.text == "← Назад" for b in flat)

    def test_next_button_on_first_page(self):
        tickets = [make_ticket(i, 1) for i in range(1, TICKETS_PER_PAGE + 1)]
        kb = tickets_keyboard(tickets, offset=0, total=TICKETS_PER_PAGE * 2)
        flat = [btn for row in kb.inline_keyboard for btn in row]
        assert any(b.text == "Далі →" for b in flat)

    def test_both_nav_buttons_on_middle_page(self):
        tickets = [make_ticket(i, 1) for i in range(1, TICKETS_PER_PAGE + 1)]
        kb = tickets_keyboard(tickets, offset=TICKETS_PER_PAGE, total=TICKETS_PER_PAGE * 3)
        flat = [btn for row in kb.inline_keyboard for btn in row]
        assert any(b.text == "← Назад" for b in flat)
        assert any(b.text == "Далі →" for b in flat)

    def test_page_counter_text(self):
        tickets = [make_ticket(i, 1) for i in range(1, TICKETS_PER_PAGE + 1)]
        kb = tickets_keyboard(tickets, offset=TICKETS_PER_PAGE, total=TICKETS_PER_PAGE * 3)
        flat = [btn for row in kb.inline_keyboard for btn in row]
        counter = next(b for b in flat if "/" in b.text)
        assert counter.text == "2/3"
        assert counter.callback_data == "noop"

    def test_cancel_button_for_active_statuses(self):
        # Статуси 1, 2, 3 — кнопка скасування є
        for status in (1, 2, 3):
            tickets = [make_ticket(42, status)]
            kb = tickets_keyboard(tickets, offset=0, total=1)
            flat = [btn for row in kb.inline_keyboard for btn in row]
            cancel_btn = next((b for b in flat if "Скасувати" in b.text), None)
            assert cancel_btn is not None, f"Кнопка скасування має бути для статусу {status}"
            assert cancel_btn.callback_data == "cancel:42"

    def test_no_cancel_button_for_closed_statuses(self):
        # Статуси 5, 6 — кнопки скасування немає
        for status in (5, 6):
            tickets = [make_ticket(42, status)]
            kb = tickets_keyboard(tickets, offset=0, total=1)
            flat = [btn for row in kb.inline_keyboard for btn in row]
            assert not any("Скасувати" in b.text for b in flat), \
                f"Кнопка скасування не має бути для статусу {status}"

    def test_detail_button_callback(self):
        tickets = [make_ticket(7, 1)]
        kb = tickets_keyboard(tickets, offset=0, total=1)
        flat = [btn for row in kb.inline_keyboard for btn in row]
        detail_btn = next(b for b in flat if b.text == "🔍 #7")
        assert detail_btn.callback_data == "tdetail:7"

    def test_pagination_callback_data(self):
        tickets = [make_ticket(i, 1) for i in range(1, TICKETS_PER_PAGE + 1)]
        kb = tickets_keyboard(tickets, offset=0, total=TICKETS_PER_PAGE * 2)
        flat = [btn for row in kb.inline_keyboard for btn in row]
        next_btn = next(b for b in flat if b.text == "Далі →")
        assert next_btn.callback_data == f"mytickets:{TICKETS_PER_PAGE}"
