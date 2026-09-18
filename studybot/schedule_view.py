from datetime import datetime, timedelta
from html import escape

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from .schedule import DAYS, MOSCOW, WEEKS, academic_week, homework_for_lesson, homework_status, lessons_on, render_day, week_type

SHORT_TYPES = {"lecture": "лек.", "seminar": "сем.", "lab": "лаб."}
SHORT_DAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")


def monday_of(day):
    return day - timedelta(days=day.weekday())


def compact_day(data, day, homework=None, icons_only=False):
    lines = [f"<b>{DAYS[day.weekday()]}, {day:%d.%m}</b>"]
    lessons = lessons_on(data, day)
    for lesson in lessons:
        discipline = lesson["discipline"]
        subject = discipline.get("shortName") or discipline["fullName"]
        kind = SHORT_TYPES.get(discipline.get("actType"), "")
        tasks = homework_for_lesson(homework, lesson, day) if homework is not None else []
        marker = ""
        if icons_only and tasks:
            marker = " ✅" if all(item["done"] for item in tasks) else " 📝"
        lines.append(f"{escape(lesson['startTime'])}–{escape(lesson['endTime'])} · <b>{escape(subject)}</b>{marker} {kind}")
        if not icons_only:
            status = homework_status(tasks)
            if status:
                lines.append(status)
    if not lessons:
        lines.append("Занятий нет 🌿")
    if icons_only and homework is not None:
        for item in homework:
            if "kind" not in item.keys() or item["kind"] != "stand":
                continue
            due = datetime.fromisoformat(item["due_at"]).astimezone(MOSCOW)
            if due.date() == day:
                status = "✅" if item["done"] else "📋"
                lines.append(f"{status} {due:%H:%M} · <b>{escape(item['subject'])}</b> · {escape(item['title'])}")
    return "\n".join(lines)


def schedule_screen(data, updated, stale, day, mode, homework=None):
    header = ""
    if stale:
        header = "⚠️ Сайт недоступен. Показываю сохранённую копию."

    def button(text, mode, target):
        return InlineKeyboardButton(text=text, callback_data=f"schedule:{mode}:{target.isoformat()}")

    monday = monday_of(day)
    if mode == "week":
        sunday = monday + timedelta(days=6)
        header += ("\n\n" if header else "") + f"<b>{monday:%d.%m.%Y} — {sunday:%d.%m.%Y}</b>\n"
        header += f"Учебная неделя {academic_week(monday)} · {WEEKS[week_type(monday)]}"
        days = [monday + timedelta(days=i) for i in range(7)]
        body = "\n\n".join(compact_day(data, target, homework, icons_only=True) for target in days)
        if len(header + body) > 3800:
            body = "\n".join(f"{DAYS[target.weekday()]}, {target:%d.%m}: занятий — {len(lessons_on(data, target))}"
                             for target in days)
        text = header + "\n\n" + body + "\n\nВыбери день для подробного расписания ↓"
        day_buttons = [button(f"{SHORT_DAYS[target.weekday()]} {target:%d.%m}", "day", target) for target in days]
        rows = [day_buttons[:4], day_buttons[4:],
                [button("← Неделя", "week", monday - timedelta(days=7)),
                 button("Неделя →", "week", monday + timedelta(days=7))]]
    else:
        body = render_day(data, day, homework)
        if len(header + body) > 3900:
            body = compact_day(data, day, homework)
        text = "\n\n".join(part for part in (header, body) if part)
        previous_day = day - timedelta(days=1)
        next_day = day + timedelta(days=1)
        rows = [[button(f"← {previous_day:%d.%m}", "day", previous_day),
                 button(f"{next_day:%d.%m} →", "day", next_day)],
                [button("🗓 Вся неделя", "week", monday)]]
        if homework is not None:
            today_homework = [item for item in homework
                              if datetime.fromisoformat(item["due_at"]).astimezone(MOSCOW).date() == day]
            linked = {item["id"] for lesson in lessons_on(data, day)
                      for item in homework_for_lesson(homework, lesson, day)}
            stand = [item for item in today_homework if "kind" in item.keys() and item["kind"] == "stand"]
            other = [item for item in today_homework if item["id"] not in linked
                     and not ("kind" in item.keys() and item["kind"] == "stand")]
            if stand:
                text += f"\n\n<b>📋 Стендовое ДЗ к сдаче</b>\nРабот: {len(stand)} · "
                text += f"не выполнено: {sum(not item['done'] for item in stand)}"
            if other:
                text += f"\n\n<b>Другие дедлайны на этот день</b>\nЗаданий: {len(other)} · "
                text += f"не выполнено: {sum(not item['done'] for item in other)}"
            for item in today_homework[:8]:
                is_stand = "kind" in item.keys() and item["kind"] == "stand"
                icon = "✅" if item["done"] else ("📋" if is_stand else "📝")
                title = item["title"] if is_stand else item["description"]
                label = f"{icon} ДЗ · {item['subject']} · {title[:30]}"
                rows.append([InlineKeyboardButton(text=label[:110], callback_data=f"hw:view:{item['id']}")])
            if len(today_homework) > 8:
                rows.append([InlineKeyboardButton(text="📚 Вся домашка", callback_data="hw:list:all:all:0")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)
