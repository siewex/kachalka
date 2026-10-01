import asyncio
import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand, CallbackQuery, Message, TelegramObject

from .config import load_config
from .db import Database
from .handlers import menu, workout
from .media import ensure_media
from .runtime import Runtime


class AccessMiddleware(BaseMiddleware):
    """Private bot: only users from ALLOWED_USERS get through; others are told their ID."""

    def __init__(self, allowed: frozenset[int]):
        self.allowed = allowed

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if user and user.id in self.allowed:
            return await handler(event, data)
        if isinstance(event, Message) and user:
            await event.answer(
                f"Это приватный бот.\nТвой Telegram ID: <code>{user.id}</code> — "
                "добавь его в ALLOWED_USERS в .env и перезапусти бота."
            )
        elif isinstance(event, CallbackQuery):
            await event.answer("Нет доступа")
        return None


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    config = load_config()
    if not config.allowed_users:
        logging.warning("ALLOWED_USERS пуст — бот будет только сообщать ID. Напиши боту и впиши свой ID в .env.")

    db = Database(config.db_path)
    await db.connect()

    bot = Bot(config.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher(storage=MemoryStorage(), db=db, rt=Runtime(), config=config)
    access = AccessMiddleware(config.allowed_users)
    dp.message.outer_middleware(access)
    dp.callback_query.outer_middleware(access)
    dp.include_routers(workout.router, menu.router)

    await bot.set_my_commands([
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="finish", description="Завершить тренировку"),
        BotCommand(command="id", description="Мой Telegram ID"),
    ])
    media_task = asyncio.create_task(ensure_media(config.media_dir))
    try:
        await dp.start_polling(bot)
    finally:
        media_task.cancel()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
