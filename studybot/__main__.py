import asyncio
import logging
from contextlib import suppress

import aiohttp
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import SimpleEventIsolation
from aiogram.types import BotCommand

from .config import Config
from .database import Database
from .handlers import create_router
from .schedule import ScheduleService


async def main():
    config = Config.load()
    db = Database(config.database)
    try:
        async with aiohttp.ClientSession() as session, Bot(
            config.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML)
        ) as bot:
            service = ScheduleService(db, config.group_uuid, session)
            dispatcher = Dispatcher(events_isolation=SimpleEventIsolation())
            dispatcher.include_router(create_router(db, config, service))
            await bot.set_my_commands([
                BotCommand(command="start", description="Начать / открыть меню"),
                BotCommand(command="schedule", description="Расписание на сегодня"),
                BotCommand(command="profile", description="Мой профиль"),
                BotCommand(command="edit_profile", description="Изменить имя и номер в журнале"),
            ])
            refresh = asyncio.create_task(service.refresh_loop())
            try:
                await dispatcher.start_polling(bot)
            finally:
                refresh.cancel()
                with suppress(asyncio.CancelledError):
                    await refresh
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(main())
    except (ValueError, KeyboardInterrupt) as error:
        print(f"Бот остановлен: {error}")
