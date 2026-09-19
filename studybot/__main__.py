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
from .mail import MailClient, MailNotifier
from .schedule import ScheduleService


async def main():
    config = Config.load()
    db = Database(config.database)
    try:
        async with aiohttp.ClientSession() as session, Bot(
            config.token, default=DefaultBotProperties(parse_mode=ParseMode.HTML)
        ) as bot:
            service = ScheduleService(db, config.group_uuid, session)
            mail_client = MailClient(config.mail_host, config.mail_port, config.mail_key)
            dispatcher = Dispatcher(events_isolation=SimpleEventIsolation())
            dispatcher.include_router(create_router(db, config, service, mail_client))
            await bot.set_my_commands([
                BotCommand(command="start", description="Открыть главное меню"),
            ])
            refresh = asyncio.create_task(service.refresh_loop())
            mail = asyncio.create_task(
                MailNotifier(db, bot, mail_client, config.mail_check_interval).run()
            )
            try:
                await dispatcher.start_polling(bot)
            finally:
                for task in (refresh, mail):
                    task.cancel()
                for task in (refresh, mail):
                    with suppress(asyncio.CancelledError):
                        await task
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        asyncio.run(main())
    except (ValueError, KeyboardInterrupt) as error:
        print(f"Бот остановлен: {error}")
