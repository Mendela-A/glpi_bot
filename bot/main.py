import asyncio
import logging

from config import CATEGORIES
from handlers import router
from middleware import callback_middleware, message_middleware
from polling import _load_bot_tickets, check_ticket_updates
from services import bot, dp, glpi

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


async def main() -> None:
    try:
        await glpi.init_session()
        log.info("GLPI сесію успішно ініціалізовано")
        CATEGORIES.update(await glpi.get_categories())
        log.info("Завантажено категорій: %d → %s", len(CATEGORIES), list(CATEGORIES.keys()))
        await _load_bot_tickets()
    except Exception as e:
        log.warning(
            "Не вдалося підключитися до GLPI при старті: %s. "
            "Буде повторна спроба при першому запиті.",
            e,
        )

    dp.message.middleware(message_middleware)
    dp.callback_query.middleware(callback_middleware)
    dp.include_router(router)

    _background_tasks: set = set()
    task = asyncio.create_task(check_ticket_updates())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await glpi.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
