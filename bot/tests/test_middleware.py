import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

os.environ.setdefault("BOT_TOKEN", "123456789:ABCdefGHIjklMNOpqrSTUvwxYZ123456789")
os.environ.setdefault("TECHNICIANS_CHAT_ID", "100")
os.environ.setdefault("GLPI_URL", "http://localhost")
os.environ.setdefault("GLPI_APP_TOKEN", "app")
os.environ.setdefault("GLPI_USER_TOKEN", "usr")

import pytest
import middleware as mw


@pytest.fixture(autouse=True)
def clear_rate_limit():
    """Очищає стан rate limiter перед кожним тестом."""
    mw._rate_limit.clear()
    yield
    mw._rate_limit.clear()


class TestRateLimit:
    def test_first_requests_not_limited(self):
        for _ in range(mw.RATE_LIMIT_REQUESTS):
            assert not mw._is_rate_limited(user_id=1)

    def test_exceeding_limit_blocks(self):
        for _ in range(mw.RATE_LIMIT_REQUESTS):
            mw._is_rate_limited(user_id=2)
        assert mw._is_rate_limited(user_id=2)

    def test_different_users_independent(self):
        for _ in range(mw.RATE_LIMIT_REQUESTS):
            mw._is_rate_limited(user_id=10)
        # user 10 заблокований, user 11 — ні
        assert mw._is_rate_limited(user_id=10)
        assert not mw._is_rate_limited(user_id=11)

    def test_old_requests_expire(self):
        user_id = 99
        now = time.monotonic()
        # Заповнюємо вікно «старими» мітками (за межами вікна)
        mw._rate_limit[user_id] = [now - mw.RATE_LIMIT_WINDOW - 1] * mw.RATE_LIMIT_REQUESTS
        # Старі записи мають очиститись → запит дозволено
        assert not mw._is_rate_limited(user_id)
