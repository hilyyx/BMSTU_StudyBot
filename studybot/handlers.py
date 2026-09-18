import time
from datetime import datetime, timedelta
from html import escape
from pathlib import Path

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandStart
from aiogram.types import FSInputFile, KeyboardButton, Message, ReplyKeyboardMarkup, ReplyKeyboardRemove

from .schedule import MOSCOW, render_day

JOURNAL_PHOTO = Path(__file__).parent / "assets" / "group_journal.jpg"

MENU = ReplyKeyboardMarkup(keyboard=[
    [KeyboardButton(text="📅 Сегодня"), KeyboardButton(text="📅 Завтра")],
    [KeyboardButton(text="🗓 Эта неделя"), KeyboardButton(text="🗓 Следующая неделя")],
    [KeyboardButton(text="👤 Мой профиль")],
], resize_keyboard=True)


def parse_number(text, maximum):
    if text.isascii() and text.isdigit():
        number = int(text)
        if 1 <= number <= maximum:
            return number
    return None


def schedule_dates(button, today):
    if button == "📅 Завтра":
        return [today + timedelta(days=1)]
    if button not in {"🗓 Эта неделя", "🗓 Следующая неделя"}:
        return [today]

    monday = today - timedelta(days=today.weekday())
    if button == "🗓 Следующая неделя":
        monday += timedelta(days=7)
    return [monday + timedelta(days=i) for i in range(7)]


async def send_day(message, text):
    # Разделяем длинный день между парами, сохраняя HTML-разметку.
    chunk = ""
    for block in text.split("\n\n"):
        if len(chunk) + len(block) + 2 > 3900 and chunk:
            await message.answer(chunk)
            chunk = ""
        chunk += ("\n\n" if chunk else "") + block
    if chunk:
        await message.answer(chunk)


def create_router(db, config, schedule):
    router = Router()
    router.message.filter(F.chat.type == ChatType.PRIVATE)
    attempts = {}

    @router.message.middleware()
    async def save_username(handler, message, data):
        user = db.user(message.from_user.id)
        if user:
            db.set_username(message.from_user.id, message.from_user.username)
        try:
            return await handler(message, data)
        finally:
            if not user:
                db.set_username(message.from_user.id, message.from_user.username)

    def menu(message):
        if message.from_user.id != config.owner_id:
            return MENU
        return ReplyKeyboardMarkup(
            keyboard=MENU.keyboard + [[KeyboardButton(text="👥 Моя группа")]],
            resize_keyboard=True,
        )

    async def prompt(message):
        user = db.user(message.from_user.id)
        if not user:
            await message.answer("Привет! Я учебный бот группы ИУ7-13Б \n"
                                 "Здесь можно смотреть расписание. Позже появится домашка.\n\n"
                                 "Отправь персональный код приглашения, полученный у владельца бота.",
                                 reply_markup=ReplyKeyboardRemove())
        elif not user["name"]:
            await message.answer("Как тебя зовут? Введи имя и фамилию.", reply_markup=ReplyKeyboardRemove())
        elif not user["journal_number"]:
            await message.answer_photo(
                photo=FSInputFile(JOURNAL_PHOTO),
                caption="Найди себя на фото журнала и введи номер своей строки: от 1 до 30. "
                        "Это также номер варианта стендового ДЗ.",
                reply_markup=ReplyKeyboardRemove(),
            )
        else:
            await message.answer(f"Привет, {escape(user['name'])}! Выбирай расписание 👇", reply_markup=menu(message))

    def ready(message):
        user = db.user(message.from_user.id)
        return bool(user and user["name"] and user["journal_number"])

    @router.message(CommandStart())
    @router.message(Command("cancel"))
    async def start(message: Message):
        if message.from_user.id == config.owner_id:
            db.add_owner(config.owner_id)
        await prompt(message)

    @router.message(Command("id"))
    async def show_id(message: Message):
        await message.answer(f"Твой Telegram ID: <code>{message.from_user.id}</code>")

    @router.message(Command("invite"))
    async def invite(message: Message):
        if message.from_user.id != config.owner_id:
            await message.answer("Создавать приглашения может только владелец.")
            return
        parts = (message.text or "").split()
        count = 1 if len(parts) == 1 else parse_number(parts[1], 28)
        if len(parts) > 2 or count is None:
            await message.answer("Используй /invite или /invite 28. Можно создать от 1 до 28 кодов.")
            return
        codes = db.invitations(count)
        await message.answer("Одноразовые приглашения — отправь каждому студенту отдельный код:\n\n" +
                             "\n".join(f"<code>{code}</code>" for code in codes))

    @router.message(Command("profile"))
    @router.message(F.text == "👤 Мой профиль")
    async def profile(message: Message):
        if not ready(message):
            await prompt(message)
            return
        user = db.user(message.from_user.id)
        await message.answer(f"<b>Твой профиль</b>\nИмя: {escape(user['name'])}\n"
                             f"Группа: ИУ7-13Б\nНомер в журнале / вариант: {user['journal_number']}\n"
                             f"Роль: {'владелец' if user['role'] == 'owner' else 'студент'}\n\n"
                             "Чтобы изменить данные, отправь /edit_profile.", reply_markup=menu(message))

    @router.message(Command("group"))
    @router.message(F.text == "👥 Моя группа")
    async def group(message: Message):
        if message.from_user.id != config.owner_id:
            await message.answer("Список группы доступен только владельцу.")
            return
        members = db.group_members()
        complete = sum(bool(user["name"] and user["journal_number"]) for user in members)
        await message.answer(
            f"<b>Группа ИУ7-13Б</b>\nРегистрацию завершили: {complete}\n"
            f"Не завершили: {len(members) - complete}\nВсего аккаунтов: {len(members)}",
            reply_markup=menu(message),
        )
        if not members:
            await message.answer("Пока никто не зарегистрировался.")
            return
        entries = []
        for user in members:
            number = user["journal_number"] or "—"
            name = escape(user["name"] or "Имя не указано")
            role = "владелец" if user["telegram_id"] == config.owner_id else "студент"
            status = "" if user["name"] and user["journal_number"] else "\nРегистрация не завершена"
            username = f"@{escape(user['username'])}" if user["username"] else "не указан"
            entries.append(f"<b>№ {number} · {name}</b>\nUsername: {username}\n"
                           f"ID: <code>{user['telegram_id']}</code> · {role}{status}")
        await send_day(message, "\n\n".join(entries))

    @router.message(Command("edit_profile"))
    async def edit_profile(message: Message):
        if not ready(message):
            await prompt(message)
            return
        db.set_name(message.from_user.id, None)
        db.set_number(message.from_user.id, None)
        await prompt(message)

    @router.message(Command("schedule"))
    @router.message(F.text.in_({"📅 Сегодня", "📅 Завтра", "🗓 Эта неделя", "🗓 Следующая неделя"}))
    async def timetable(message: Message):
        if not ready(message):
            await prompt(message)
            return
        await message.answer("Загружаю расписание…")
        try:
            data, updated, stale = await schedule.get()
        except RuntimeError:
            await message.answer("Сайт расписания сейчас недоступен, а сохранённой копии ещё нет. Попробуй позже.",
                                 reply_markup=menu(message))
            return
        today = datetime.now(MOSCOW).date()
        days = schedule_dates(message.text, today)
        header = f"<b>Расписание {escape(data['title'])}</b>\nОбновлено: {datetime.fromisoformat(updated):%d.%m %H:%M} (МСК)"
        if stale:
            header += "\n⚠️ Не удалось обновить. Показываю сохранённую копию."
        await message.answer(header, reply_markup=menu(message))
        for day in days:
            await send_day(message, render_day(data, day))

    @router.message()
    async def onboarding(message: Message):
        user_id = message.from_user.id
        user = db.user(user_id)
        text = (message.text or "").strip()
        if not text or text.startswith("/"):
            await message.answer("Используй /start или кнопки меню. При регистрации отправляй данные текстом.")
            return
        if not user:
            now = time.monotonic()
            recent = [stamp for stamp in attempts.get(user_id, []) if now - stamp < 60]
            if len(recent) >= 5:
                await message.answer("Слишком много попыток. Подожди минуту и попробуй снова.")
                return
            attempts[user_id] = recent + [now]
            if not db.redeem(text, user_id):
                await message.answer("Код не найден или уже использован. Проверь его или обратись к владельцу.")
                return
            attempts.pop(user_id, None)
            await message.answer("Приглашение принято ✅")
            await prompt(message)
        elif not user["name"]:
            if not 2 <= len(text) <= 100 or not any(char.isalpha() for char in text):
                await message.answer("Введи имя и фамилию текстом, от 2 до 100 символов.")
                return
            db.set_name(user_id, text)
            await prompt(message)
        elif not user["journal_number"]:
            number = parse_number(text, 30)
            if number is None:
                await message.answer("Номер должен быть целым числом от 1 до 30.")
                return
            if not db.set_number(user_id, number):
                await message.answer("Этот номер в журнале уже занят. Проверь свой номер. "
                                     "Если его занял другой студент по ошибке, обратись к владельцу бота.")
                return
            await message.answer("Регистрация завершена ✅", reply_markup=menu(message))
            await prompt(message)
        else:
            await message.answer("Выбирай действие в меню 👇", reply_markup=menu(message))

    return router
