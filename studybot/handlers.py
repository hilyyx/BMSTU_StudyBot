import time
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandStart
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile, Message, ReplyKeyboardRemove

from .schedule import MOSCOW
from .schedule_view import monday_of, schedule_screen
from .homework import register_homework
from .navigation import CANCEL, CANCEL_PROFILE, EDIT_PROFILE, HOME, Navigation
from .materials import materials_keyboard

JOURNAL_PHOTO = Path(__file__).parent / "assets" / "group_journal.jpg"

def parse_number(text, maximum):
    if text.isascii() and text.isdigit():
        number = int(text)
        if 1 <= number <= maximum:
            return number
    return None


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
    nav = Navigation(db, config.owner_id)

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

    async def prompt(message):
        user = db.user(message.from_user.id)
        if not user:
            await message.answer("Привет! Я учебный бот группы ИУ7-13Б \n"
                                 "Здесь можно смотреть расписание и домашку.\n\n"
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
            await nav.show(message, message.from_user.id, "main", f"Привет, {escape(user['name'])}! Выбирай раздел 👇")

    def ready(message):
        user = db.user(message.from_user.id)
        return bool(user and user["name"] and user["journal_number"])

    @router.message(CommandStart())
    @router.message(Command("cancel"))
    @router.message(F.text.in_({HOME, CANCEL}))
    async def start(message: Message):
        if message.text == CANCEL or (message.text and message.text.split()[0].split('@')[0] == "/cancel"):
            db.clear_draft(message.from_user.id)
            await message.answer("Черновик ДЗ отменён.")
        if message.from_user.id == config.owner_id:
            db.add_owner(config.owner_id)
        await prompt(message)

    @router.callback_query(F.data == "nav:main")
    async def main_menu(query):
        await query.answer()
        user = db.user(query.from_user.id)
        if not query.message or query.message.chat.type != ChatType.PRIVATE:
            return
        if not user or not user["name"] or not user["journal_number"]:
            await query.message.answer("Сначала заверши регистрацию в личном чате.")
            return
        await nav.show(query.message, query.from_user.id, "main", "Главное меню 👇")

    @router.message(Command("id"))
    async def show_id(message: Message):
        await message.answer(f"Твой Telegram ID: <code>{message.from_user.id}</code>")

    @router.message(Command("invite"))
    @router.message(F.text.in_({"🎟 Одно приглашение", "🎟 28 приглашений"}))
    async def invite(message: Message):
        if message.from_user.id != config.owner_id:
            await message.answer("Создавать приглашения может только владелец.")
            return
        if message.text in {"🎟 Одно приглашение", "🎟 28 приглашений"}:
            count = 28 if message.text == "🎟 28 приглашений" else 1
        else:
            parts = (message.text or "").split()
            count = 1 if len(parts) == 1 else parse_number(parts[1], 28)
            if len(parts) > 2:
                count = None
        if count is None:
            await message.answer("Выбери число приглашений кнопками в разделе «🎟 Приглашения».")
            return
        codes = db.invitations(count)
        await nav.show(message, message.from_user.id, "invitations",
                       "Одноразовые приглашения — отправь каждому студенту отдельный код:\n\n" +
                       "\n".join(f"<code>{code}</code>" for code in codes))

    @router.message(F.text == "🎟 Приглашения")
    async def invitations(message: Message):
        if message.from_user.id != config.owner_id:
            await message.answer("Создавать приглашения может только владелец.")
            return
        await nav.show(message, message.from_user.id, "invitations", "Сколько приглашений создать?")

    @router.message(Command("profile"))
    @router.message(F.text == "👤 Мой профиль")
    @router.message(F.text == CANCEL_PROFILE)
    async def profile(message: Message):
        if not ready(message):
            await prompt(message)
            return
        user = db.user(message.from_user.id)
        await nav.show(message, message.from_user.id, "profile", f"<b>Твой профиль</b>\nИмя: {escape(user['name'])}\n"
                             f"Группа: ИУ7-13Б\nНомер в журнале / вариант: {user['journal_number']}\n"
                             f"Роль: {'владелец' if user['role'] == 'owner' else 'студент'}\n"
                             f"Telegram ID: <code>{user['telegram_id']}</code>")

    @router.message(Command("group"))
    @router.message(F.text == "👥 Моя группа")
    async def group(message: Message):
        if message.from_user.id != config.owner_id:
            await message.answer("Список группы доступен только владельцу.")
            return
        members = db.group_members()
        complete = sum(bool(user["name"] and user["journal_number"]) for user in members)
        await nav.show(message, message.from_user.id, "group",
            f"<b>Группа ИУ7-13Б</b>\nРегистрацию завершили: {complete}\n"
            f"Не завершили: {len(members) - complete}\nВсего аккаунтов: {len(members)}",
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
    @router.message(F.text == EDIT_PROFILE)
    async def edit_profile(message: Message):
        if not ready(message):
            await prompt(message)
            return
        db.start_profile_edit(message.from_user.id)
        await nav.show(message, message.from_user.id, "edit_profile", "Введи новое имя и фамилию.")

    async def editing_profile(message):
        return db.profile_edit(message.from_user.id) is not None

    @router.message(editing_profile)
    async def profile_input(message: Message):
        user_id = message.from_user.id
        edit = db.profile_edit(user_id)
        text = (message.text or "").strip()
        if text.startswith("/"):
            await message.answer("Введи данные текстом или нажми «Отменить изменения».")
            return
        if not edit["name"]:
            if not 2 <= len(text) <= 100 or not any(char.isalpha() for char in text):
                await message.answer("Введи имя и фамилию текстом, от 2 до 100 символов.")
                return
            db.set_profile_edit_name(user_id, text)
            await message.answer_photo(
                FSInputFile(JOURNAL_PHOTO), caption="Найди себя на фото и введи номер своей строки (1–30).",
                reply_markup=nav.keyboard("edit_profile", user_id),
            )
            return
        number = parse_number(text, 30)
        if number is None:
            await message.answer("Номер должен быть целым числом от 1 до 30.")
            return
        if not db.finish_profile_edit(user_id, number):
            await message.answer("Этот номер уже занят. Проверь свой номер или обратись к владельцу.")
            return
        await profile(message)

    @router.message(F.text == "📖 Учебные материалы")
    async def materials(message: Message):
        if not ready(message):
            await prompt(message)
            return
        await nav.enter(message, message.from_user.id, "materials", "Учебные материалы 👇")
        await message.answer("<b>📖 Учебные материалы</b>\n\n"
                             "Выбери предмет — откроется его папка на Яндекс Диске.\n"
                             "Там можно посмотреть или скачать учебники, конспекты и другие файлы.",
                             reply_markup=materials_keyboard())

    @router.message(F.text == "📅 Расписание")
    async def schedule_menu(message: Message):
        if not ready(message):
            await prompt(message)
            return
        await nav.show(message, message.from_user.id, "schedule", "Какое расписание открыть?")

    @router.message(Command("schedule"))
    @router.message(F.text.in_({"📅 Сегодня", "📅 Завтра", "🗓 Эта неделя", "🗓 Следующая неделя"}))
    async def timetable(message: Message):
        if not ready(message):
            await prompt(message)
            return
        await nav.enter(message, message.from_user.id, "schedule", "Расписание 👇")
        try:
            data, updated, stale = await schedule.get()
        except RuntimeError:
            await message.answer("Сайт расписания сейчас недоступен, а сохранённой копии ещё нет. Попробуй позже.",
                                 reply_markup=nav.keyboard("schedule", message.from_user.id))
            return
        today = datetime.now(MOSCOW).date()
        mode = "week" if message.text in {"🗓 Эта неделя", "🗓 Следующая неделя"} else "day"
        day = monday_of(today) if mode == "week" else today
        if message.text == "📅 Завтра":
            day += timedelta(days=1)
        elif message.text == "🗓 Следующая неделя":
            day += timedelta(days=7)
        homework = db.homework_list(message.from_user.id)
        text, buttons = schedule_screen(data, updated, stale, day, mode, homework)
        await message.answer(text, reply_markup=buttons)

    @router.callback_query(F.data.startswith("schedule:"))
    async def navigate_schedule(query):
        user = db.user(query.from_user.id)
        if not user or not user["name"] or not user["journal_number"]:
            await query.answer("Сначала заверши регистрацию.", show_alert=True)
            return
        if not query.message or query.message.chat.type != ChatType.PRIVATE:
            await query.answer()
            return
        try:
            _, mode, raw_day = query.data.split(":")
            day = date.fromisoformat(raw_day)
            if mode not in {"day", "week"}:
                raise ValueError
        except ValueError:
            await query.answer("Кнопка недоступна.")
            return
        await query.answer()
        await nav.enter(query.message, query.from_user.id, "schedule", "Расписание 👇")
        try:
            data, updated, stale = await schedule.get()
        except RuntimeError:
            await query.message.answer("Не удалось загрузить расписание. Попробуй позже.")
            return
        homework = db.homework_list(query.from_user.id)
        text, buttons = schedule_screen(data, updated, stale, day, mode, homework)
        try:
            await query.message.edit_text(text, reply_markup=buttons)
        except TelegramBadRequest as error:
            if "message is not modified" not in error.message:
                raise

    register_homework(router, db, config, schedule, nav)

    @router.message()
    async def onboarding(message: Message):
        user_id = message.from_user.id
        user = db.user(user_id)
        text = (message.text or "").strip()
        if not text or text.startswith("/"):
            await message.answer("Пользуйся кнопками меню. При регистрации отправляй данные текстом.")
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
            await message.answer("Регистрация завершена ✅")
            await prompt(message)
        else:
            await nav.show(message, user_id, "main", "Выбирай раздел в меню 👇")

    return router
