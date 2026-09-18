from datetime import datetime, timedelta

from .schedule import MOSCOW, SEMESTER_START, week_type


def week_dates(number):
    monday = SEMESTER_START + timedelta(weeks=number - 1)
    return monday, monday + timedelta(days=6)


def lessons_in_week(data, subject, number, kind, now):
    monday, _ = week_dates(number)
    options = set()
    for lesson in data["schedule"]:
        discipline = lesson["discipline"]
        if discipline["fullName"] != subject or discipline.get("actType") != kind:
            continue
        day = monday + timedelta(days=lesson["day"] - 1)
        if lesson["week"] not in {"all", week_type(day)}:
            continue
        hour, minute = map(int, lesson["startTime"].split(":"))
        due = datetime(day.year, day.month, day.day, hour, minute, tzinfo=MOSCOW)
        if due > now:
            options.add(due)
    return sorted(options)
