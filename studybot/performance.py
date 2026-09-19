import json
import urllib.error
import urllib.parse
import urllib.request
from html import escape
from html.parser import HTMLParser
from http.cookiejar import CookieJar
from urllib.parse import urlparse


LOGIN_URL = (
    "https://lks.bmstu.ru/portal4/cookie/login?"
    "back=https%3A%2F%2Flks.bmstu.ru%2Fprofile&profile_any=true"
)
API_URL = "https://lks.bmstu.ru/lks-back/api/v1"


class LksLoginError(Exception):
    pass


class LoginFormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.action = None

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "form" and values.get("id") == "kc-form-login":
            self.action = values.get("action")


class PerformanceClient:
    def __init__(self, timeout=20):
        self.timeout = timeout

    def load(self, username, password):
        cookies = CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))
        opener.addheaders = [("User-Agent", "BMSTU-StudyBot/1.0")]
        try:
            login_page = opener.open(LOGIN_URL, timeout=self.timeout)
            parser = LoginFormParser()
            parser.feed(login_page.read().decode("utf-8", errors="replace"))
            if not parser.action or urlparse(parser.action).hostname != "sso.bmstu.ru":
                raise ConnectionError("Не удалось открыть форму входа МГТУ")
            form = urllib.parse.urlencode({
                "username": username,
                "password": password,
                "credentialId": "",
                "login": "Sign In",
            }).encode()
            result = opener.open(urllib.request.Request(parser.action, data=form), timeout=self.timeout)
            result.read()
            if urlparse(result.geturl()).hostname == "sso.bmstu.ru":
                raise LksLoginError("Личный кабинет не принял логин или пароль")
            student = self.get_json(opener, f"{API_URL}/student")
            stages = student.get("stages") if isinstance(student, dict) else None
            if not stages or not isinstance(stages[0], dict) or not stages[0].get("uuid"):
                raise ConnectionError("Личный кабинет не вернул учебный профиль")
            stage_uuid = stages[0]["uuid"]
            progress = self.get_json(opener, f"{API_URL}/student/{stage_uuid}/progress")
            sessions = self.get_json(opener, f"{API_URL}/student/{stage_uuid}/sessions")
            return progress, sessions
        except LksLoginError:
            raise
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise LksLoginError("Личный кабинет не принял логин или пароль") from error
            raise ConnectionError(f"Личный кабинет ответил с ошибкой {error.code}") from error
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as error:
            raise ConnectionError("Личный кабинет временно недоступен") from error

    def get_json(self, opener, url):
        response = opener.open(url, timeout=self.timeout)
        return json.loads(response.read().decode("utf-8"))


STAGES = {
    "": "не проставлено",
    "0": "пропущено",
    "1": "выдано",
    "2": "выполнено",
    "3": "защищено",
    "4": "посещено",
    "12": "работа на семинаре",
}


def progress_student(payload):
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data", payload)
    if isinstance(data, dict):
        return data.get("student", data)
    return {}


def control_text(item):
    week = item.get("week")
    title = item.get("type") or "работа"
    value = item.get("value")
    points = item.get("points") if isinstance(item.get("points"), dict) else {}
    if value not in (None, ""):
        if title == "М" and points.get("point_all") is not None:
            title += f" {value}/{points['point_all']}"
        else:
            title += f" {value}%"
    stage = STAGES.get(str(item.get("stage", "")), str(item.get("stage") or ""))
    prefix = f"{week} нед.: " if week else ""
    return f"{prefix}{title}" + (f" · {stage}" if stage else "")


def render_performance(progress, sessions):
    student = progress_student(progress)
    disciplines = student.get("disciplines", []) if isinstance(student, dict) else []
    blocks = ["<b>Текущая успеваемость</b>"]
    for discipline in disciplines:
        if not isinstance(discipline, dict):
            continue
        rows = []
        for item in discipline.get("controls") or []:
            if isinstance(item, dict):
                rows.append(control_text(item))
        labs = [item for item in discipline.get("laboratory") or [] if isinstance(item, dict)]
        if labs:
            protected = sum(str(item.get("stage", "")) == "3" for item in labs)
            rows.append(f"Лабораторные: защищено {protected} из {len(labs)}")
        seminars = [item for item in discipline.get("seminars") or [] if isinstance(item, dict)]
        if seminars:
            attended = sum(str(item.get("stage", "")) in {"4", "12"} for item in seminars)
            rows.append(f"Семинары: отмечено {attended} из {len(seminars)}")
        if rows:
            blocks.append(f"<b>{escape(str(discipline.get('title') or 'Предмет'))}</b>\n" +
                          "\n".join(escape(row) for row in rows))
    if len(blocks) == 1:
        blocks.append("Данных о текущем контроле пока нет.")

    session_data = sessions.get("data", sessions) if isinstance(sessions, dict) else sessions
    if isinstance(session_data, list):
        marks = []
        for term in session_data:
            if not isinstance(term, dict):
                continue
            for item in term.get("marks") or []:
                if not isinstance(item, dict):
                    continue
                subject = item.get("disciplineName")
                mark = item.get("mark") or item.get("title")
                if subject and mark:
                    marks.append(f"{escape(str(subject))}: <b>{escape(str(mark))}</b>")
        if marks:
            blocks.append("<b>Результаты сессий</b>\n" + "\n".join(marks))
    return "\n\n".join(blocks)
