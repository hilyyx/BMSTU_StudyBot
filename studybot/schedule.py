import asyncio
import json
import logging
from datetime import date, datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

import aiohttp

MOSCOW = ZoneInfo("Europe/Moscow")
DAYS = ("Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье")
TYPES = {"lecture": "Лекция", "seminar": "Семинар", "lab": "Лабораторная", "generated": "Самостоятельная работа"}
WEEKS = {"ch": "числитель", "zn": "знаменатель"}

SEMESTER_START = date(2026, 8, 31) #1 неделя - числитель


def academic_week(day: date, semester_start: date = SEMESTER_START):
    monday = day - timedelta(days=day.weekday())
    return (monday - semester_start).days // 7 + 1


def week_type(day: date):
    return "ch" if academic_week(day) % 2 == 1 else "zn"


def lessons_on(data, day):
    lessons = [item for item in data["schedule"] if item["day"] == day.isoweekday()
               and item["week"] in {"all", week_type(day)}]
    return sorted(lessons, key=lambda item: (item["startTime"], item["time"]))


def homework_for_lesson(homework, lesson, day):
    subject = lesson["discipline"]["fullName"]
    matches = []
    for item in homework:
        if "kind" in item.keys() and item["kind"] == "stand":
            continue
        due = datetime.fromisoformat(item["due_at"]).astimezone(MOSCOW)
        if item["subject"] != subject or due.date() != day:
            continue
        if (item["lesson_type"] == "manual" and lesson["discipline"].get("actType") != "lecture") or (
            item["lesson_type"] == lesson["discipline"].get("actType")
            and due.strftime("%H:%M") == lesson["startTime"]
        ):
            matches.append(item)
    return matches


def homework_status(items):
    if not items:
        return ""
    completed = sum(bool(item["done"]) for item in items)
    if completed == len(items):
        return f"ДЗ: {len(items)} · всё выполнено ✅"
    return f"ДЗ: {len(items)} · осталось выполнить: {len(items) - completed} 📝"


def render_day(data, day, homework=None):
    current = week_type(day)
    lines = [f"<b>{DAYS[day.weekday()]}, {day:%d.%m.%Y}</b>"]
    lines.append(f"Учебная неделя {academic_week(day)} · {WEEKS[current]}")
    lessons = lessons_on(data, day)
    for item in lessons:
        discipline = item["discipline"]
        label = TYPES.get(discipline.get("actType"), "Занятие")
        lines.append(f"\n<b>{escape(item['startTime'])}–{escape(item['endTime'])}</b> · {escape(label)}")
        lines.append(f"<b>{escape(discipline['fullName'])}</b>")
        if homework is not None:
            status = homework_status(homework_for_lesson(homework, item, day))
            if status:
                lines.append(status)
        rooms = ", ".join(room["name"] for room in item.get("audiences", []) if room.get("name"))
        if rooms:
            lines.append(f"Аудитория: {escape(rooms)}")
        names = []
        for teacher in item.get("teachers", []):
            name = " ".join(teacher.get(field) or "" for field in ("lastName", "firstName", "middleName"))
            names.append(name.strip())
        teachers = "; ".join(names)
        if teachers:
            lines.append(escape(teachers))
    if not lessons:
        lines.append("Занятий нет 🌿")
    return "\n".join(lines)


def validate_payload(payload):
    data = payload["data"]
    if not isinstance(data["title"], str) or not isinstance(data["schedule"], list):
        raise ValueError("Некорректный ответ расписания")
    for item in data["schedule"]:
        if item["day"] not in range(1, 8) or item["week"] not in {"all", "ch", "zn"}:
            raise ValueError("Некорректная пара")
        for key in ("startTime", "endTime"):
            datetime.strptime(item[key], "%H:%M")
        if not isinstance(item["time"], int) or not isinstance(item["discipline"]["fullName"], str):
            raise ValueError("Некорректный предмет")
    return data


class ScheduleService:
    def __init__(self, db, group_uuid, session):
        self.db = db
        self.group_uuid = group_uuid
        self.session = session
        self.lock = asyncio.Lock()
        self.retry_after = None

    async def get(self, force=False):
        async with self.lock:
            cached = self.db.cache(self.group_uuid)
            now = datetime.now(MOSCOW)
            fresh = cached and now - datetime.fromisoformat(cached["updated_at"]) < timedelta(hours=1)
            if not force and (fresh or (self.retry_after and now < self.retry_after)):
                if cached:
                    return json.loads(cached["payload"]), cached["updated_at"], not fresh
                raise RuntimeError("Расписание временно недоступно")
            try:
                url = f"https://lks.bmstu.ru/lks-back/api/v1/schedules/groups/{self.group_uuid}/public"
                async with self.session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as response:
                    response.raise_for_status()
                    data = validate_payload(await response.json())
                updated = now.isoformat()
                self.db.save_cache(self.group_uuid, json.dumps(data, ensure_ascii=False), updated)
                self.retry_after = None
                return data, updated, False
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError, KeyError, TypeError):
                logging.getLogger(__name__).warning("Не удалось обновить расписание")
                self.retry_after = now + timedelta(minutes=5)
                if cached:
                    return json.loads(cached["payload"]), cached["updated_at"], True
                raise RuntimeError("Расписание временно недоступно") from None

    async def refresh_loop(self):
        while True:
            try:
                await self.get()
            except RuntimeError:
                pass
            await asyncio.sleep(300)
