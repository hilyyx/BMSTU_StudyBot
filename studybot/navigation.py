from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

HOME = "🏠 Главное меню"
BACK = "↩️ Назад"
NEXT = "✅ Дальше"
CANCEL = "❌ Отменить"
RESUME = "📝 Продолжить черновик"
EDIT_PROFILE = "✏️ Изменить профиль"
CANCEL_PROFILE = "↩️ Отменить изменения"


def reply_keyboard(rows):
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=text) for text in row] for row in rows],
        resize_keyboard=True,
    )


class Navigation:
    def __init__(self, db, owner_id):
        self.db = db
        self.owner_id = owner_id
        self.sections = {}
        self.homework_lists = {}

    def keyboard(self, section, user_id):
        if section == "main":
            rows = [["📅 Расписание"], ["📚 Домашка", "📋 Стендовое ДЗ"],
                    ["📝 Невыполненное", "✅ Выполненное"],
                    ["📖 Учебные материалы"],
                    ["🔴 Просроченное", "👤 Мой профиль"],
                    ["💡 Предложения и идеи"]]
            if self.db.draft(user_id):
                rows.append([RESUME])
            if user_id == self.owner_id:
                rows.append(["👥 Моя группа", "📥 Предложения"])
            return reply_keyboard(rows)
        rows = {
            "profile": [[EDIT_PROFILE], [HOME]],
            "edit_profile": [[CANCEL_PROFILE], [HOME]],
            "schedule": [["📅 Сегодня", "📅 Завтра"], ["🗓 Эта неделя", "🗓 Следующая неделя"], [HOME]],
            "regular": [["➕ Добавить ДЗ"], ["📝 Невыполненное", "✅ Выполненное"], [HOME]],
            "stand": [["➕ Добавить стендовое ДЗ"], ["📝 Невыполненное", "✅ Выполненное"], [HOME]],
            "pending": [["✅ Выполненное"], [HOME]],
            "completed": [["📝 Невыполненное"], [HOME]],
            "overdue": [[HOME]],
            "materials": [[HOME]],
            "feedback": [["✍️ Отправить предложение"], [HOME]],
            "feedback_input": [[CANCEL], [HOME]],
            "feedback_inbox": [["💡 Предложения и идеи"], [HOME]],
            "group": [["🎟 Приглашения"], [HOME]],
            "invitations": [["🎟 Одно приглашение", "🎟 28 приглашений"], ["👥 Моя группа"], [HOME]],
            "draft_content": [[NEXT], [BACK, CANCEL], [HOME]],
            "draft": [[BACK, CANCEL], [HOME]],
        }
        return reply_keyboard(rows[section])

    async def show(self, message, user_id, section, text):
        if section != "edit_profile":
            self.db.cancel_profile_edit(user_id)
        if section != "feedback_input":
            self.db.cancel_feedback(user_id)
        self.sections[user_id] = section
        await message.answer(text, reply_markup=self.keyboard(section, user_id))

    async def enter(self, message, user_id, section, text):
        if self.sections.get(user_id) != section:
            await self.show(message, user_id, section, text)
