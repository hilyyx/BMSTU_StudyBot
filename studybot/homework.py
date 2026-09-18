import hashlib
import json
import secrets
from datetime import datetime, timedelta
from html import escape

from aiogram import F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from .schedule import MOSCOW, TYPES, week_type


def keyboard(rows):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=text, callback_data=data) for text, data in row]
        for row in rows
    ])


def subject_key(subject):
    return hashlib.sha256(subject.encode()).hexdigest()[:12]


def next_lesson(data, subject, lesson_type, now):
    for offset in range(15):
        day = now.date() + timedelta(days=offset)
        lessons = [item for item in data["schedule"]
                   if item["discipline"]["fullName"] == subject
                   and item["discipline"].get("actType") == lesson_type
                   and item["day"] == day.isoweekday()
                   and item["week"] in {"all", week_type(day)}]
        for lesson in sorted(lessons, key=lambda item: item["startTime"]):
            hour, minute = map(int, lesson["startTime"].split(":"))
            due = datetime(day.year, day.month, day.day, hour, minute, tzinfo=MOSCOW)
            if due > now:
                return due
    return None


def parse_deadline(text):
    for fmt in ("%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            due = datetime.strptime(text, fmt).replace(tzinfo=MOSCOW)
            return due if "%H" in fmt else due.replace(hour=23, minute=59)
        except ValueError:
            continue
    return None


def homework_card(item, now=None):
    now = now or datetime.now(MOSCOW)
    due = datetime.fromisoformat(item["due_at"])
    if item.get("deleted"):
        status = "Удалено"
    elif item.get("done"):
        status = "Выполнено ✅"
    elif due < now:
        status = "Просрочено 🔴"
    else:
        status = "Не выполнено"
    attachments = item["attachments"]
    if isinstance(attachments, str):
        attachments = json.loads(attachments)
    lesson = TYPES.get(item["lesson_type"], "Своя дата")
    author = item.get("author_name") or "Имя не указано"
    return (f"<b>{escape(item['subject'])}</b>\n\n{escape(item['description'])}\n\n"
            f"Срок: {due:%d.%m.%Y %H:%M} (МСК) · {lesson}\n"
            f"Сдача: {escape(item['delivery'])}\nВложений: {len(attachments)}\n"
            f"Автор: {escape(author)}\nСтатус: {status}")


def register_homework(router, db, config, schedule):
    def registered(user_id):
        user = db.user(user_id)
        return bool(user and user["name"] and user["journal_number"])

    def can_edit(item, user_id):
        return item and (item["author_id"] == user_id or user_id == config.owner_id)

    async def show_item(message, homework_id, user_id, show_attachments=True):
        row = db.homework(homework_id, user_id)
        if not row:
            await message.answer("Задание не найдено.")
            return
        item = dict(row)
        if item["deleted"] and not can_edit(item, user_id):
            await message.answer("Задание удалено.")
            return
        rows = []
        if not item["deleted"]:
            rows.append([("↩️ Не выполнено" if item["done"] else "✅ Выполнено", f"hw:done:{homework_id}")])
        if can_edit(item, user_id):
            if item["deleted"]:
                rows.append([("Восстановить", f"hw:restore:{homework_id}")])
            else:
                rows.append([("Изменить", f"hw:edit:{homework_id}"), ("Удалить", f"hw:delete:{homework_id}")])
        if show_attachments and not item["deleted"]:
            for attachment in json.loads(item["attachments"]):
                if attachment["type"] == "photo":
                    await message.answer_photo(attachment["file_id"])
                else:
                    await message.answer_document(attachment["file_id"])
        await message.answer(homework_card(item), reply_markup=keyboard(rows) if rows else None)

    async def show_list(message, user_id, mode="all", subject=None, page=0):
        items = db.homework_list(user_id, subject=subject,
                                 overdue_at=datetime.now(MOSCOW).isoformat() if mode == "overdue" else None,
                                 deleted=mode == "deleted")
        if mode == "deleted":
            items = [item for item in items if can_edit(item, user_id)]
        if not items:
            await message.answer("Заданий пока нет." if mode != "overdue" else "Просроченного ДЗ нет 🎉")
            return
        page = max(0, min(page, (len(items) - 1) // 8))
        rows = []
        for item in items[page * 8:page * 8 + 8]:
            due = datetime.fromisoformat(item["due_at"])
            label = f"{'✅' if item['done'] else '📝'} {due:%d.%m} · {item['subject']} · {item['description'][:35]}"
            rows.append([(label[:110], f"hw:view:{item['id']}")])
        key = subject_key(subject) if subject else "all"
        navigation = []
        if page:
            navigation.append(("←", f"hw:list:{mode}:{key}:{page - 1}"))
        if (page + 1) * 8 < len(items):
            navigation.append(("→", f"hw:list:{mode}:{key}:{page + 1}"))
        if navigation:
            rows.append(navigation)
        await message.answer(f"<b>{escape(subject or {'overdue': 'Просроченное ДЗ', 'deleted': 'Удалённые задания'}.get(mode, 'Вся домашка'))}</b>\n"
                             f"Заданий: {len(items)} · Страница {page + 1}", reply_markup=keyboard(rows))

    async def begin(message, user_id, edit_id=None):
        existing = db.draft(user_id)
        if existing:
            await message.answer("У тебя уже есть черновик. Продолжи его через /draft или отмени через /cancel.")
            return
        item = db.homework(edit_id, user_id) if edit_id else None
        if edit_id and (not can_edit(item, user_id) or item["deleted"]):
            await message.answer("Редактировать можно только свои задания. Владелец может редактировать любые.")
            return
        try:
            data, _, stale = await schedule.get()
        except RuntimeError:
            await message.answer("Не удалось получить предметы из расписания. Попробуй позже.")
            return
        subjects = sorted({lesson["discipline"]["fullName"] for lesson in data["schedule"]
                           if lesson["discipline"].get("actType") in {"seminar", "lecture", "lab"}})
        if not subjects:
            await message.answer("В расписании пока нет предметов для ДЗ.")
            return
        draft = {"step": "subject", "token": secrets.token_hex(5), "subjects": subjects,
                 "description": "", "attachments": [], "schedule": data}
        if item:
            draft.update(edit_id=item["id"], version=item["version"])
        db.save_draft(user_id, draft)
        if stale:
            await message.answer("Использую сохранённое расписание: сайт временно недоступен.")
        await show_draft(message, user_id)

    async def show_draft(message, user_id):
        draft = db.draft(user_id)
        if not draft:
            await message.answer("Черновика нет. Нажми «➕ Добавить ДЗ».")
            return
        step, token = draft["step"], draft["token"]
        if step == "subject":
            rows = [[(subject, f"hw:subject:{token}:{i}")] for i, subject in enumerate(draft["subjects"])]
            await message.answer("Выбери предмет. Отменить добавление: /cancel.", reply_markup=keyboard(rows))
        elif step == "content":
            await message.answer("Отправь описание ДЗ, фотографии или файлы. Можно несколькими сообщениями.\n"
                                 "Когда всё добавишь, отправь /done. Отмена: /cancel.")
        elif step == "due":
            now = datetime.now(MOSCOW)
            options = {}
            rows = []
            for kind in ("seminar", "lab", "lecture"):
                due = next_lesson(draft["schedule"], draft["subject"], kind, now)
                if due:
                    options[kind] = due.isoformat()
                    label = f"{'⭐ ' if kind == 'seminar' else ''}{TYPES[kind]} · {due:%d.%m %H:%M}"
                    rows.append([(label, f"hw:due:{token}:{kind}")])
            draft["due_options"] = options
            db.save_draft(user_id, draft)
            rows.append([("Указать дату вручную", f"hw:due:{token}:manual")])
            await message.answer("Выбери срок сдачи. По умолчанию предлагаю ближайший семинар.\n"
                                 "Срок фиксируется при публикации и не меняется вместе с расписанием.", reply_markup=keyboard(rows))
        elif step == "manual_due":
            await message.answer("Введи срок: ДД.ММ.ГГГГ ЧЧ:ММ (время Москвы).\n"
                                 "Если указать только дату, срок будет 23:59 этого дня.")
        elif step == "delivery":
            await message.answer("Как сдавать? Выбери «В тетради» или отправь пояснение текстом, например:\n"
                                 "В тетради и загрузить в Moodle: https://…\n"
                                 "Можно указать несколько способов и ссылку на диск.",
                                 reply_markup=keyboard([[('В тетради', f"hw:delivery:{token}:notebook")]]))
        elif step == "confirm":
            preview = dict(draft, author_name=db.user(user_id)["name"])
            await message.answer("<b>Проверь перед публикацией</b>\n\n" + homework_card(preview),
                                 reply_markup=keyboard([[('Опубликовать', f"hw:publish:{token}"),
                                                         ('Отменить', f"hw:cancel:{token}")]]))

    @router.message(Command("add_homework"))
    @router.message(F.text == "➕ Добавить ДЗ")
    async def add(message: Message):
        if not registered(message.from_user.id):
            await message.answer("Сначала заверши регистрацию через /start.")
            return
        await begin(message, message.from_user.id)

    @router.message(Command("draft"))
    async def resume(message: Message):
        if registered(message.from_user.id):
            await show_draft(message, message.from_user.id)
        else:
            await message.answer("Сначала заверши регистрацию через /start.")

    @router.message(Command("homework"))
    @router.message(F.text == "📚 Домашка")
    async def subjects(message: Message):
        if not registered(message.from_user.id):
            await message.answer("Сначала заверши регистрацию через /start.")
            return
        names = db.homework_subjects()
        rows = [[(name, f"hw:list:all:{subject_key(name)}:0")] for name in names]
        rows.append([("Все задания", "hw:list:all:all:0"), ("Удалённые", "hw:list:deleted:all:0")])
        await message.answer("Выбери предмет:", reply_markup=keyboard(rows))

    @router.message(Command("overdue"))
    @router.message(F.text == "🔴 Просроченное")
    async def overdue(message: Message):
        if not registered(message.from_user.id):
            await message.answer("Сначала заверши регистрацию через /start.")
            return
        await show_list(message, message.from_user.id, "overdue")

    @router.message(Command("done"))
    async def finish_content(message: Message):
        draft = db.draft(message.from_user.id)
        if not registered(message.from_user.id) or not draft or draft["step"] != "content":
            await message.answer("Команда /done нужна после добавления описания и вложений ДЗ.")
            return
        if not draft["description"] and not draft["attachments"]:
            await message.answer("Сначала отправь описание или хотя бы одно вложение.")
            return
        if not draft["description"]:
            draft["description"] = "Задание во вложениях"
        draft["step"] = "due"
        db.save_draft(message.from_user.id, draft)
        await show_draft(message, message.from_user.id)

    async def has_draft(message):
        return db.draft(message.from_user.id) is not None

    @router.message(has_draft)
    async def draft_input(message: Message):
        user_id = message.from_user.id
        if not registered(user_id):
            await message.answer("Сначала заверши регистрацию через /start.")
            return
        draft = db.draft(user_id)
        text = (message.text or message.caption or "").strip()
        if text.startswith("/"):
            await message.answer("Продолжить черновик: /draft. Закончить вложения: /done. Отменить: /cancel.")
            return
        if draft["step"] == "content":
            attachment = None
            if message.photo:
                attachment = {"type": "photo", "file_id": message.photo[-1].file_id}
            elif message.document:
                attachment = {"type": "document", "file_id": message.document.file_id}
            if not text and not attachment:
                await message.answer("Поддерживаются текст, фотографии и документы.")
                return
            description = "\n".join(part for part in (draft["description"], text) if part)
            if len(description) > 1800 or len(escape(description)) > 2000 or (attachment and len(draft["attachments"]) >= 10):
                await message.answer("Максимум 1800 символов описания и 10 вложений. Это сообщение не добавлено.")
                return
            draft["description"] = description
            if attachment:
                draft["attachments"].append(attachment)
            db.save_draft(user_id, draft)
            await message.answer("Добавлено. Ещё вложения или /done для выбора срока.")
            return
        if draft["step"] == "manual_due":
            due = parse_deadline(text)
            if not due or due <= datetime.now(MOSCOW):
                await message.answer("Укажи будущую дату в формате ДД.ММ.ГГГГ ЧЧ:ММ.")
                return
            draft.update(due_at=due.isoformat(), lesson_type="manual", step="delivery")
        elif draft["step"] == "delivery":
            if not message.text or not 1 <= len(text) <= 500 or len(escape(text)) > 700:
                await message.answer("Отправь способ сдачи текстом, не более 500 символов.")
                return
            draft.update(delivery=text, step="confirm")
        else:
            await show_draft(message, user_id)
            return
        db.save_draft(user_id, draft)
        await show_draft(message, user_id)

    @router.callback_query(F.data.startswith("hw:"))
    async def actions(query):
        user_id = query.from_user.id
        if not registered(user_id) or not query.message or query.message.chat.type != "private":
            await query.answer("Сначала заверши регистрацию в личном чате.", show_alert=True)
            return
        await query.answer()
        message = query.message
        parts = query.data.split(":")
        action = parts[1]
        if action in {"subject", "due", "delivery", "publish", "cancel"}:
            draft = db.draft(user_id)
            if not draft or draft["token"] != parts[2]:
                await message.answer("Эта кнопка устарела. Продолжить текущий черновик: /draft.")
                return
            if action == "cancel":
                db.clear_draft(user_id)
                await message.answer("Черновик отменён.")
                return
            if action == "subject" and draft["step"] == "subject":
                index = int(parts[3])
                if not 0 <= index < len(draft["subjects"]):
                    return
                draft.update(subject=draft["subjects"][index], step="content")
            elif action == "due" and draft["step"] == "due":
                kind = parts[3]
                if kind == "manual":
                    draft["step"] = "manual_due"
                elif kind in draft["due_options"]:
                    due = draft["due_options"][kind]
                    if datetime.fromisoformat(due) <= datetime.now(MOSCOW):
                        await show_draft(message, user_id)
                        return
                    draft.update(due_at=due, lesson_type=kind, step="delivery")
                else:
                    return
            elif action == "delivery" and draft["step"] == "delivery":
                draft.update(delivery="В тетради", step="confirm")
            elif action == "publish" and draft["step"] == "confirm":
                if datetime.fromisoformat(draft["due_at"]) <= datetime.now(MOSCOW):
                    draft["step"] = "due"
                    db.save_draft(user_id, draft)
                    await message.answer("Срок уже прошёл. Выбери новый.")
                    await show_draft(message, user_id)
                    return
                homework_id = db.publish_homework(user_id, config.owner_id, parts[2])
                if homework_id is None:
                    await message.answer("Задание уже изменили или удалили. Отмени черновик и начни заново.")
                    return
                await message.answer("ДЗ опубликовано для всей группы ✅")
                await show_item(message, homework_id, user_id)
                return
            else:
                await message.answer("Этот шаг уже завершён. Продолжить: /draft.")
                return
            db.save_draft(user_id, draft)
            await show_draft(message, user_id)
            return
        if action == "list":
            mode, key, page = parts[2:5]
            if mode not in {"all", "overdue", "deleted"}:
                return
            subject = None
            if key != "all":
                subject = next((name for name in db.homework_subjects() if subject_key(name) == key), None)
                if subject is None:
                    await message.answer("По этому предмету больше нет заданий.")
                    return
            await show_list(message, user_id, mode, subject, int(page))
            return
        homework_id = int(parts[2])
        item = db.homework(homework_id, user_id)
        if not item or (item["deleted"] and not can_edit(item, user_id)):
            await message.answer("Задание не найдено или удалено.")
            return
        if action in {"edit", "delete", "delete_yes", "restore"} and not can_edit(item, user_id):
            await message.answer("Изменять чужие задания может только владелец.")
            return
        if action == "edit":
            await message.answer("Заполни новую карточку целиком. Старая останется до подтверждения.")
            await begin(message, user_id, homework_id)
        elif action == "delete":
            await message.answer("Удалить задание для всей группы? Его можно будет восстановить.",
                                 reply_markup=keyboard([[('Да, удалить', f"hw:delete_yes:{homework_id}"),
                                                         ('Оставить', f"hw:view:{homework_id}")]]))
        elif action in {"delete_yes", "restore"}:
            db.set_homework_deleted(homework_id, user_id, config.owner_id, action == "delete_yes")
            await show_item(message, homework_id, user_id)
        elif action == "done":
            db.toggle_homework_done(homework_id, user_id)
            await show_item(message, homework_id, user_id, show_attachments=False)
        elif action in {"view", "files"}:
            await show_item(message, homework_id, user_id)
