from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.redis import RedisStorage

from config import BOT_TOKEN, REDIS_URL
from glpi_client import GLPIClient

bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode="HTML"))
dp = Dispatcher(storage=RedisStorage.from_url(REDIS_URL))
glpi = GLPIClient()
