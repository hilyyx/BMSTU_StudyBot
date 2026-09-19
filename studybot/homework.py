import hashlib
import json
import secrets
from datetime import datetime, timedelta
from html import escape

from aiogram import F
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from .schedule import MOSCOW, TYPES, academic_week, week_type
from .stand_homework import lessons_in_week, week_dates
from .navigation import BACK, NEXT, RESUME


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


def homework_card(item, now=None, variant=None):
    now = now or datetime.now(MOSCOW)
    due = datetime.fromisoformat(item["due_at"])
    if item.get("done"):
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
    details = ""
    if item.get("kind") == "stand":
        details = f"\n<b>Стендовое ДЗ · {escape(item['title'])}</b>"
        if variant is not None:
            details += f"\nТвой вариант — №{variant}"
        if item.get("due_week"):
            details += f"\nНеделя сдачи: {item['due_week']}"
    deadline = f"{due:%d.%m.%Y}" if item.get("kind") == "stand" else f"{due:%d.%m.%Y %H:%M} (МСК)"
    return (f"<b>{escape(item['subject'])}</b>{details}\n\n{escape(item['description'])}\n\n"
            f"Срок: {deadline} · {lesson}\n"
            f"Сдача: {escape(item['delivery'])}\nВложений: {len(attachments)}\n"
            f"Автор: {escape(author)}\nСтатус: {status}")


def homework_preview(item, limit=1200):
    attachments = json.loads(item["attachments"]) if isinstance(item["attachments"], str) else item["attachments"]
    description = (item["description"] or "").strip()
    if attachments and description == "Задание во вложениях":
        description = ""
    escaped = []
    length = 0
    for char in description:
        value = escape(char)
        if length + len(value) > limit:
            escaped.append("…")
            break
        escaped.append(value)
        length += len(value)
    text = "".join(escaped)
    if attachments:
        attachment_note = "📎 Есть вложения." if text else "📎 ДЗ во вложении."
        text = f"{text}\n{attachment_note}" if text else attachment_note
    return text or "Описание не указано."


def register_homework(router, db, config, schedule, nav):
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
        section = "stand" if item["kind"] == "stand" else "regular"
        context = nav.homework_lists.get(user_id) if nav.sections.get(user_id) in {"regular", "stand", "overdue", "completed", "pending"} else None
        if context is None:
            context = (item["kind"], item["subject"], 0)
        elif context[0] in {"completed", "pending", "overdue"}:
            section = context[0]
        await nav.enter(message, user_id, section, "Карточка ДЗ 👇")
        rows = []
        rows.append([("↩️ Не выполнено" if item["done"] else "✅ Выполнено", f"hw:done:{homework_id}")])
        if can_edit(item, user_id):
            rows.append([("Изменить", f"hw:edit:{homework_id}"), ("Удалить", f"hw:delete:{homework_id}")])
        mode, subject, page = context
        key = subject_key(subject) if subject else "all"
        rows.append([("↩️ К заданиям", f"hw:list:{mode}:{key}:{page}")])
        if show_attachments:
            for attachment in json.loads(item["attachments"]):
                if attachment["type"] == "photo":
                    await message.answer_photo(attachment["file_id"])
                else:
                    await message.answer_document(attachment["file_id"])
        await message.answer(homework_card(item, variant=db.user(user_id)["journal_number"]),
                             reply_markup=keyboard(rows) if rows else None)

    async def show_list(message, user_id, mode="all", subject=None, page=0):
        section = "stand" if mode == "stand_upcoming" else mode if mode in {"regular", "stand", "overdue", "completed", "pending"} else "regular"
        await nav.enter(message, user_id, section, "Список заданий 👇")
        nav.homework_lists[user_id] = (mode, subject, page)
        items = db.homework_list(user_id, subject=subject,
                                 overdue_at=datetime.now(MOSCOW).isoformat() if mode == "overdue" else None,
                                 due_from=datetime.now(MOSCOW).isoformat() if mode in {"upcoming", "stand_upcoming"} else None,
                                 kind="stand" if mode == "stand_upcoming" else mode if mode in {"regular", "stand"} else None,
                                 done=True if mode == "completed" else False if mode in {"regular", "stand", "pending", "upcoming", "stand_upcoming"} else None)
        if not items:
            await message.answer({"overdue": "Просроченного ДЗ нет 🎉", "completed": "Выполненных заданий пока нет.",
                                  "pending": "Невыполненного ДЗ нет 🎉", "regular": "Невыполненных обычных заданий нет 🎉",
                                  "stand": "Невыполненных стендовых заданий нет 🎉",
                                  "stand_upcoming": "Ближайших стендовых ДЗ пока нет 🎉",
                                  "upcoming": "Ближайших дедлайнов пока нет 🎉"}.get(mode, "Заданий пока нет."))
            return
        page = max(0, min(page, (len(items) - 1) // 8))
        nav.homework_lists[user_id] = (mode, subject, page)
        rows = []
        page_items = items[page * 8:page * 8 + 8]
        blocks = []
        for item in page_items:
            due = datetime.fromisoformat(item["due_at"])
            status = "✅" if item["done"] else "📋" if item["kind"] == "stand" else "📝"
            prefix = f"{status} Стендовое ДЗ · " if item["kind"] == "stand" else f"{status} "
            label = f"{prefix}{due:%d.%m} · {item['subject']} · {(item['title'] or item['description'])[:35]}"
            rows.append([(label[:110], f"hw:view:{item['id']}")])
            heading_prefix = "Стендовое ДЗ · " if item["kind"] == "stand" else ""
            heading = f"{heading_prefix}{due:%d.%m} · <b>{escape(item['subject'])}</b>"
            if item["kind"] == "stand" and item["title"]:
                heading += f" · {escape(item['title'])}"
            blocks.append(f"{heading}\n{homework_preview(item)}")
        key = subject_key(subject) if subject else "all"
        navigation = []
        if page:
            navigation.append(("←", f"hw:list:{mode}:{key}:{page - 1}"))
        if (page + 1) * 8 < len(items):
            navigation.append(("→", f"hw:list:{mode}:{key}:{page + 1}"))
        if navigation:
            rows.append(navigation)
        subjects_mode = "stand" if mode == "stand_upcoming" else mode if mode in {"stand", "completed", "pending"} else "pending" if mode == "upcoming" else "regular"
        rows.append([("↩️ К предметам", f"hw:subjects:{subjects_mode}")])
        title = escape(subject or {'overdue': 'Просроченное ДЗ',
                       'stand': 'Стендовое ДЗ', 'regular': 'Обычное ДЗ', 'completed': 'Выполненное ДЗ',
                       'stand_upcoming': 'Ближайшее стендовое ДЗ',
                       'pending': 'Невыполненное ДЗ', 'upcoming': 'Ближайшие дедлайны'}.get(mode, 'Вся домашка'))
        chunks = []
        current = f"<b>{title}</b>\nЗаданий: {len(items)} · Страница {page + 1}"
        for block in blocks:
            if len(current) + len(block) + 2 > 3800:
                chunks.append(current)
                current = block
            else:
                current += "\n\n" + block
        chunks.append(current)
        for index, chunk in enumerate(chunks):
            await message.answer(chunk, reply_markup=keyboard(rows) if index == len(chunks) - 1 else None)

    async def begin(message, user_id, edit_id=None, kind="regular"):
        existing = db.draft(user_id)
        if existing:
            await message.answer("У тебя уже есть черновик. Продолжим его; для нового задания сначала нажми «Отменить».")
            await show_draft(message, user_id)
            return
        item = db.homework(edit_id, user_id) if edit_id else None
        if edit_id and not can_edit(item, user_id):
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
                 "description": "", "attachments": [], "schedule": data,
                 "kind": item["kind"] if item else kind, "title": ""}
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
        section = "draft_content" if step == "content" else "draft"
        await nav.enter(message, user_id, section, "Добавление ДЗ 👇")
        if step == "subject":
            rows = [[(subject, f"hw:subject:{token}:{i}")] for i, subject in enumerate(draft["subjects"])]
            await message.answer("Выбери предмет:", reply_markup=keyboard(rows))
        elif step == "content":
            instruction = "Прикрепи общий файл с вариантами. Можно добавить описание и другие вложения.\n" if draft.get("kind") == "stand" else "Отправь описание ДЗ, фотографии или файлы. Можно несколькими сообщениями.\n"
            await message.answer(instruction +
                                 "Когда всё добавишь, нажми «✅ Дальше».")
        elif step == "stand_title":
            await message.answer("Как называется стендовая работа? Например: «ДЗ №1 — Графики функций».")
        elif step == "stand_week":
            await message.answer("Введи номер учебной недели сдачи (1–52), например 6.\n"
                                 f"Сейчас идёт учебная неделя {academic_week(datetime.now(MOSCOW).date())}. "
                                 "После выбора недели покажу её даты и пары.")
        elif step == "stand_kind":
            monday, sunday = week_dates(draft["due_week"])
            await message.answer(f"<b>Неделя {draft['due_week']}: {monday:%d.%m} — {sunday:%d.%m.%Y}</b>\n"
                                 "На какой паре будете сдавать?",
                                 reply_markup=keyboard([[('Семинар', f"hw:stand_kind:{token}:seminar"),
                                                         ('Лабораторная', f"hw:stand_kind:{token}:lab")],
                                                        [('Другая неделя', f"hw:stand_week:{token}")]]))
        elif step == "stand_pair":
            options = lessons_in_week(draft["schedule"], draft["subject"], draft["due_week"],
                                      draft["lesson_type"], datetime.now(MOSCOW))
            draft["stand_options"] = [due.isoformat() for due in options]
            db.save_draft(user_id, draft)
            rows = [[(f"{'⭐ ' if index == 0 else ''}{TYPES[draft['lesson_type']]} · {due:%d.%m %H:%M}",
                      f"hw:stand_pair:{token}:{due:%Y%m%d%H%M}")] for index, due in enumerate(options)]
            rows.extend([[('Другая неделя', f"hw:stand_week:{token}"),
                          ('Другой тип пары', f"hw:stand_kind_back:{token}")],
                         [('Дата вручную', f"hw:stand_manual:{token}")]])
            text = "Выбери пару сдачи. ⭐ — первая подходящая пара на этой неделе." if options else "На выбранной неделе нет будущих пар этого типа. Выбери другую неделю, тип пары или укажи дату вручную."
            await message.answer(text, reply_markup=keyboard(rows))
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
            user = db.user(user_id)
            preview = dict(draft, author_name=user["name"])
            await message.answer("<b>Проверь перед публикацией</b>\n\n" + homework_card(preview, variant=user["journal_number"]),
                                 reply_markup=keyboard([[('Опубликовать', f"hw:publish:{token}"),
                                                         ('Отменить', f"hw:cancel:{token}")]]))

    @router.message(Command("add_homework"))
    @router.message(F.text == "➕ Добавить ДЗ")
    async def add(message: Message):
        if not registered(message.from_user.id):
            await message.answer("Сначала заверши регистрацию в личном чате.")
            return
        await begin(message, message.from_user.id)

    @router.message(Command("add_stand_homework"))
    @router.message(F.text == "➕ Добавить стендовое ДЗ")
    async def add_stand(message: Message):
        if not registered(message.from_user.id):
            await message.answer("Сначала заверши регистрацию в личном чате.")
            return
        await begin(message, message.from_user.id, kind="stand")

    @router.message(Command("draft"))
    @router.message(F.text == RESUME)
    async def resume(message: Message):
        if registered(message.from_user.id):
            await show_draft(message, message.from_user.id)
        else:
            await message.answer("Сначала заверши регистрацию в личном чате.")

    async def show_subjects(message, user_id, kind):
        await nav.enter(message, user_id, kind, {"stand": "Стендовое ДЗ 👇", "regular": "Обычное ДЗ 👇",
                                               "completed": "Выполненное ДЗ 👇", "pending": "Невыполненное ДЗ 👇"}[kind])
        nav.homework_lists.pop(user_id, None)
        items = db.homework_list(user_id, kind=kind if kind in {"regular", "stand"} else None,
                                 done=kind == "completed")
        names = sorted({item["subject"] for item in items})
        rows = [[(name, f"hw:list:{kind}:{subject_key(name)}:0")] for name in names]
        rows.append([("Все выполненные" if kind == "completed" else "Все невыполненные", f"hw:list:{kind}:all:0")])
        await message.answer("Выбери предмет:", reply_markup=keyboard(rows))

    @router.message(Command("homework"))
    @router.message(F.text == "📚 Домашка")
    async def subjects(message: Message):
        if not registered(message.from_user.id):
            await message.answer("Сначала заверши регистрацию в личном чате.")
            return
        await show_list(message, message.from_user.id, "upcoming")

    @router.message(Command("stand_homework"))
    @router.message(F.text == "📋 Стендовое ДЗ")
    async def stand_subjects(message: Message):
        if not registered(message.from_user.id):
            await message.answer("Сначала заверши регистрацию в личном чате.")
            return
        await show_list(message, message.from_user.id, "stand_upcoming")

    @router.message(F.text.in_({"✅ Выполненное", "📝 Невыполненное"}))
    async def personal_homework(message: Message):
        if not registered(message.from_user.id):
            await message.answer("Сначала заверши регистрацию в личном чате.")
            return
        await show_subjects(message, message.from_user.id,
                            "completed" if message.text == "✅ Выполненное" else "pending")

    @router.message(Command("overdue"))
    @router.message(F.text == "🔴 Просроченное")
    async def overdue(message: Message):
        if not registered(message.from_user.id):
            await message.answer("Сначала заверши регистрацию в личном чате.")
            return
        await show_list(message, message.from_user.id, "overdue")

    @router.message(Command("done"))
    @router.message(F.text == NEXT)
    async def finish_content(message: Message):
        draft = db.draft(message.from_user.id)
        if not registered(message.from_user.id) or not draft or draft["step"] != "content":
            await message.answer("Нажми «Дальше» после добавления описания и вложений ДЗ.")
            return
        if not draft["description"] and not draft["attachments"]:
            await message.answer("Сначала отправь описание или хотя бы одно вложение.")
            return
        if draft.get("kind") == "stand" and not any(item["type"] == "document" for item in draft["attachments"]):
            await message.answer("Для стендового ДЗ нужен общий файл с вариантами. Отправь его как документ.")
            return
        if not draft["description"]:
            draft["description"] = "Задание во вложениях"
        draft["step"] = "stand_week" if draft.get("kind") == "stand" else "due"
        db.save_draft(message.from_user.id, draft)
        await show_draft(message, message.from_user.id)

    @router.message(F.text == BACK)
    async def previous_step(message: Message):
        user_id = message.from_user.id
        draft = db.draft(user_id)
        if not registered(user_id) or not draft:
            await message.answer("Вернуться в разделы можно кнопкой «Главное меню».")
            return
        previous = {
            "stand_title": "subject", "content": "stand_title" if draft.get("kind") == "stand" else "subject",
            "due": "content", "stand_week": "content", "stand_kind": "stand_week", "stand_pair": "stand_kind",
            "manual_due": "stand_pair" if draft.get("kind") == "stand" else "due",
            "delivery": "stand_pair" if draft.get("kind") == "stand" else "due", "confirm": "delivery",
        }
        if draft["step"] == "subject":
            await nav.show(message, user_id, "main", "Черновик сохранён. Главное меню 👇")
            return
        draft["step"] = previous[draft["step"]]
        db.save_draft(user_id, draft)
        await show_draft(message, user_id)

    async def has_draft(message):
        return db.draft(message.from_user.id) is not None

    @router.message(has_draft)
    async def draft_input(message: Message):
        user_id = message.from_user.id
        if not registered(user_id):
            await message.answer("Сначала заверши регистрацию в личном чате.")
            return
        draft = db.draft(user_id)
        text = (message.text or message.caption or "").strip()
        if text.startswith("/"):
            await message.answer("Пользуйся кнопками «Дальше», «Назад» и «Отменить».")
            return
        if draft["step"] == "stand_title":
            if not message.text or not 1 <= len(text) <= 100 or len(escape(text)) > 150:
                await message.answer("Введи название работы текстом, до 100 символов.")
                return
            draft.update(title=text, step="content")
            db.save_draft(user_id, draft)
            await show_draft(message, user_id)
            return
        if draft["step"] == "stand_week":
            if not text.isascii() or not text.isdigit() or not 1 <= int(text) <= 52:
                await message.answer("Введи номер учебной недели числом от 1 до 52.")
                return
            number = int(text)
            _, sunday = week_dates(number)
            if sunday < datetime.now(MOSCOW).date():
                await message.answer("Эта неделя уже прошла. Выбери текущую или будущую неделю.")
                return
            draft.update(due_week=number, step="stand_kind")
            db.save_draft(user_id, draft)
            await show_draft(message, user_id)
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
            await message.answer("Добавлено. Отправь ещё вложения или нажми «✅ Дальше» для выбора срока.")
            return
        if draft["step"] == "manual_due":
            due = parse_deadline(text)
            if not due or due <= datetime.now(MOSCOW):
                await message.answer("Укажи будущую дату в формате ДД.ММ.ГГГГ ЧЧ:ММ.")
                return
            draft.update(due_at=due.isoformat(), lesson_type="manual", step="delivery")
            if draft.get("kind") == "stand":
                draft["due_week"] = academic_week(due.date())
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
        if action == "new" and parts[2] in {"regular", "stand"}:
            await begin(message, user_id, kind=parts[2])
            return
        if action == "subjects" and parts[2] in {"regular", "stand", "completed", "pending"}:
            await show_subjects(message, user_id, parts[2])
            return
        if action in {"subject", "due", "delivery", "publish", "cancel", "stand_kind", "stand_pair",
                      "stand_week", "stand_kind_back", "stand_manual"}:
            draft = db.draft(user_id)
            if not draft or draft["token"] != parts[2]:
                await message.answer("Эта кнопка устарела. Нажми «Продолжить черновик» в главном меню.")
                return
            if action == "cancel":
                db.clear_draft(user_id)
                await nav.show(message, user_id, "main", "Черновик отменён. Главное меню 👇")
                return
            if action == "subject" and draft["step"] == "subject":
                index = int(parts[3])
                if not 0 <= index < len(draft["subjects"]):
                    return
                draft.update(subject=draft["subjects"][index],
                             step="stand_title" if draft.get("kind") == "stand" else "content")
            elif action == "stand_kind" and draft["step"] == "stand_kind" and parts[3] in {"seminar", "lab"}:
                draft.update(lesson_type=parts[3], step="stand_pair")
            elif action == "stand_pair" and draft["step"] == "stand_pair":
                due = next((value for value in draft["stand_options"]
                            if datetime.fromisoformat(value).strftime("%Y%m%d%H%M") == parts[3]), None)
                if due is None:
                    await message.answer("Эта пара больше недоступна. Выбери срок из актуального списка.")
                    await show_draft(message, user_id)
                    return
                if datetime.fromisoformat(due) <= datetime.now(MOSCOW):
                    await show_draft(message, user_id)
                    return
                draft.update(due_at=due, step="delivery")
            elif action == "stand_week" and draft["step"] in {"stand_kind", "stand_pair"}:
                draft["step"] = "stand_week"
            elif action == "stand_kind_back" and draft["step"] == "stand_pair":
                draft["step"] = "stand_kind"
            elif action == "stand_manual" and draft["step"] == "stand_pair":
                draft["step"] = "manual_due"
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
                    draft["step"] = "stand_week" if draft.get("kind") == "stand" else "due"
                    db.save_draft(user_id, draft)
                    await message.answer("Срок уже прошёл. Выбери новый.")
                    await show_draft(message, user_id)
                    return
                homework_id = db.publish_homework(user_id, config.owner_id, parts[2])
                if homework_id is None:
                    await message.answer("Задание уже изменили или удалили. Отмени черновик и начни заново.")
                    return
                section = "stand" if draft.get("kind") == "stand" else "regular"
                await nav.show(message, user_id, section, "ДЗ опубликовано для всей группы ✅")
                await show_item(message, homework_id, user_id)
                return
            else:
                await message.answer("Этот шаг уже завершён. Продолжи черновик кнопками.")
                return
            db.save_draft(user_id, draft)
            await show_draft(message, user_id)
            return
        if action == "list":
            mode, key, page = parts[2:5]
            if mode not in {"all", "overdue", "regular", "stand", "completed", "pending", "upcoming", "stand_upcoming"}:
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
        if not item:
            await message.answer("Задание не найдено или удалено.")
            return
        if action in {"edit", "delete", "delete_yes"} and not can_edit(item, user_id):
            await message.answer("Изменять чужие задания может только владелец.")
            return
        if action == "edit":
            await message.answer("Заполни новую карточку целиком. Старая останется до подтверждения.")
            await begin(message, user_id, homework_id)
        elif action == "delete":
            await message.answer("Удалить задание для всей группы без возможности восстановления?",
                                 reply_markup=keyboard([[('Да, удалить', f"hw:delete_yes:{homework_id}"),
                                                         ('Оставить', f"hw:view:{homework_id}")]]))
        elif action == "delete_yes":
            if db.delete_homework(homework_id, user_id, config.owner_id):
                await nav.show(message, user_id, "stand" if item["kind"] == "stand" else "regular",
                               "Задание удалено окончательно.")
            else:
                await message.answer("Задание уже удалено или недоступно.")
        elif action == "done":
            db.toggle_homework_done(homework_id, user_id)
            await show_item(message, homework_id, user_id, show_attachments=False)
        elif action in {"view", "files"}:
            await show_item(message, homework_id, user_id)
