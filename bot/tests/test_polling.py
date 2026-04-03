import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("BOT_TOKEN", "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ123456789")
os.environ.setdefault("TECHNICIANS_CHAT_ID", "100")
os.environ.setdefault("GLPI_URL", "http://localhost")
os.environ.setdefault("GLPI_APP_TOKEN", "app")
os.environ.setdefault("GLPI_USER_TOKEN", "usr")

import re
import pytest
from config import GLPI_FIELD_CONTENT, GLPI_FIELD_ID, GLPI_FIELD_STATUS


# Логіку парсингу [tg:ID] виносимо окремо, щоб тестувати незалежно від мережі
def parse_tg_tag(content: str) -> int | None:
    match = re.search(r'\[tg:(\d+)\]', content)
    return int(match.group(1)) if match else None


def build_ticket_row(ticket_id: int, user_id: int, status: int) -> dict:
    return {
        GLPI_FIELD_ID: str(ticket_id),
        GLPI_FIELD_CONTENT: f"Опис проблеми\n\n[tg:{user_id}]",
        GLPI_FIELD_STATUS: str(status),
    }


class TestTgTagParsing:
    def test_valid_tag(self):
        assert parse_tg_tag("текст\n\n[tg:123456]") == 123456

    def test_tag_at_start(self):
        assert parse_tg_tag("[tg:1]") == 1

    def test_no_tag_returns_none(self):
        assert parse_tg_tag("звичайний текст без тегу") is None

    def test_tag_with_extra_text(self):
        assert parse_tg_tag("опис\nТелефон: +380501234567\n\n[tg:987654321]") == 987654321

    def test_malformed_tag_ignored(self):
        assert parse_tg_tag("[tg:]") is None
        assert parse_tg_tag("[tg:abc]") is None


class TestBuildBotTicketsCache:
    """Симулює логіку _load_bot_tickets без мережі."""

    def _load(self, rows: list[dict]) -> tuple[dict, dict]:
        bot_tickets: dict[int, int] = {}
        status_cache: dict[int, int] = {}
        for ticket in rows:
            try:
                ticket_id = int(ticket.get(GLPI_FIELD_ID))
            except (TypeError, ValueError):
                continue
            user_id = parse_tg_tag(ticket.get(GLPI_FIELD_CONTENT, ""))
            if user_id is None:
                continue
            status = int(ticket.get(GLPI_FIELD_STATUS, 1))
            bot_tickets[ticket_id] = user_id
            status_cache[ticket_id] = status
        return bot_tickets, status_cache

    def test_single_ticket(self):
        rows = [build_ticket_row(ticket_id=10, user_id=555, status=2)]
        bt, sc = self._load(rows)
        assert bt == {10: 555}
        assert sc == {10: 2}

    def test_multiple_tickets(self):
        rows = [
            build_ticket_row(1, 111, 1),
            build_ticket_row(2, 222, 5),
        ]
        bt, sc = self._load(rows)
        assert bt == {1: 111, 2: 222}
        assert sc[1] == 1
        assert sc[2] == 5

    def test_ticket_without_tag_skipped(self):
        rows = [
            {GLPI_FIELD_ID: "99", GLPI_FIELD_CONTENT: "без тегу", GLPI_FIELD_STATUS: "1"},
        ]
        bt, sc = self._load(rows)
        assert bt == {}

    def test_invalid_id_skipped(self):
        rows = [
            {GLPI_FIELD_ID: "not-a-number", GLPI_FIELD_CONTENT: "[tg:123]", GLPI_FIELD_STATUS: "1"},
        ]
        bt, sc = self._load(rows)
        assert bt == {}


class TestPrePopulateNotifiedFollowups:
    """Перевіряє, що існуючі follow-up IDs потрапляють у _notified_followups при старті."""

    def _collect_followup_ids(self, followups_per_ticket: dict[int, list[dict]]) -> set[int]:
        """Симулює логіку pre-populate з _load_bot_tickets."""
        notified: set[int] = set()
        for followups in followups_per_ticket.values():
            for fu in followups:
                fu_id = fu.get("id")
                if fu_id:
                    notified.add(fu_id)
        return notified

    def test_existing_followups_are_collected(self):
        followups = {
            6: [{"id": 101, "content": "Solution approved"}, {"id": 102, "content": "OK"}],
            13: [{"id": 201, "content": "3123"}],
        }
        notified = self._collect_followup_ids(followups)
        assert notified == {101, 102, 201}

    def test_followup_without_id_skipped(self):
        followups = {
            6: [{"content": "без id"}, {"id": 55, "content": "з id"}],
        }
        notified = self._collect_followup_ids(followups)
        assert notified == {55}

    def test_empty_tickets_gives_empty_set(self):
        notified = self._collect_followup_ids({})
        assert notified == set()

    def test_ticket_with_no_followups(self):
        notified = self._collect_followup_ids({10: []})
        assert notified == set()

    def test_new_followup_after_startup_not_in_set(self):
        """Follow-up, що з'явився після старту, не має бути в pre-populated set."""
        existing_followups = {6: [{"id": 101, "content": "старий"}]}
        notified = self._collect_followup_ids(existing_followups)
        # новий follow-up ID 999 відсутній → буде відправлений як новий
        assert 999 not in notified
