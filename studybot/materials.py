from urllib.parse import quote

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

DISK_URL = "https://disk.yandex.ru/d/xJfR1TQctuos5g"

# Названия папок на общем Диске: регистр и пробелы важны для ссылок.
SUBJECT_FOLDERS = (
    ("Аналитическая геометрия", "Аналитическая Геометрия"),
    ("Английский язык", "Английский язык"),
    ("Инженерная графика", "Инженерная Графика"),
    ("История", "История"),
    ("Математический анализ", "Математический Анализ"),
)


def materials_keyboard():
    rows = [[InlineKeyboardButton(text=f"📁 {subject}", url=f"{DISK_URL}/{quote(folder, safe='')}")]
            for subject, folder in SUBJECT_FOLDERS]
    rows.append([InlineKeyboardButton(text="🔗 Весь Яндекс Диск", url=DISK_URL)])
    return InlineKeyboardMarkup(inline_keyboard=rows)
