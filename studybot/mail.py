import asyncio
import imaplib
import logging
from contextlib import suppress
from dataclasses import dataclass
from email import policy
from email.header import decode_header
from email.parser import BytesParser
from email.utils import parseaddr
from html import escape

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from cryptography.fernet import Fernet, InvalidToken


log = logging.getLogger(__name__)


@dataclass(frozen=True)
class MailMessage:
    uid: int
    sender: str
    subject: str
    preview: str
    has_attachments: bool


class MailLoginError(Exception):
    pass


def decode_text(value):
    if not value:
        return ""
    parts = []
    for part, charset in decode_header(value):
        if isinstance(part, bytes):
            for encoding in (charset, "utf-8", "cp1251", "latin1"):
                if not encoding:
                    continue
                try:
                    part = part.decode(encoding, errors="replace")
                    break
                except (LookupError, UnicodeError):
                    continue
        parts.append(str(part))
    return "".join(parts).strip()


def message_preview(message, limit=700):
    candidates = []
    attachments = False
    for part in message.walk() if message.is_multipart() else (message,):
        disposition = part.get_content_disposition()
        if disposition == "attachment" or part.get_filename():
            attachments = True
            continue
        if part.get_content_type() != "text/plain":
            continue
        try:
            text = part.get_content()
        except (LookupError, UnicodeError):
            payload = part.get_payload(decode=True) or b""
            text = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if isinstance(text, str):
            candidates.append(text)
    text = " ".join(" ".join(candidates).split())
    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text, attachments


class MailClient:
    def __init__(self, host, port, key):
        self.host = host
        self.port = port
        self.cipher = Fernet(key.encode()) if key else None

    @property
    def enabled(self):
        return self.cipher is not None

    def encrypt(self, password):
        if not self.cipher:
            raise RuntimeError("Шифрование почты не настроено")
        return self.cipher.encrypt(password.encode()).decode()

    def decrypt(self, encrypted_password):
        if not self.cipher:
            raise RuntimeError("Шифрование почты не настроено")
        try:
            return self.cipher.decrypt(encrypted_password.encode()).decode()
        except InvalidToken as error:
            raise RuntimeError("Не удалось расшифровать пароль") from error

    def connect(self, address, password):
        try:
            connection = imaplib.IMAP4_SSL(self.host, self.port, timeout=20)
            connection.login(address, password)
            return connection
        except imaplib.IMAP4.error as error:
            raise MailLoginError("Неверный адрес или пароль") from error
        except (OSError, TimeoutError) as error:
            raise ConnectionError("Почтовый сервер временно недоступен") from error

    def initial_cursor(self, address, password):
        connection = self.connect(address, password)
        try:
            uid_validity, latest_uid = self.select_cursor(connection)
            return uid_validity, latest_uid
        finally:
            with suppress(Exception):
                connection.logout()

    @staticmethod
    def select_cursor(connection):
        status, _ = connection.select("INBOX", readonly=True)
        if status != "OK":
            raise ConnectionError("Не удалось открыть входящие письма")
        validity = connection.response("UIDVALIDITY")[1]
        uid_validity = int(validity[0]) if validity and validity[0] else None
        status, data = connection.uid("search", None, "ALL")
        if status != "OK":
            raise ConnectionError("Не удалось прочитать список писем")
        uids = [int(value) for value in (data[0] or b"").split()]
        return uid_validity, max(uids, default=0)

    def new_messages(self, address, password, stored_validity, last_uid, limit=20):
        connection = self.connect(address, password)
        try:
            uid_validity, latest_uid = self.select_cursor(connection)
            if stored_validity is not None and uid_validity != stored_validity:
                return uid_validity, latest_uid, []
            status, data = connection.uid("search", None, f"UID {last_uid + 1}:*")
            if status != "OK":
                raise ConnectionError("Не удалось проверить новые письма")
            uids = [int(value) for value in (data[0] or b"").split() if int(value) > last_uid]
            messages = []
            for uid in uids[:limit]:
                # Заголовки и первые 64 КиБ текста достаточно для уведомления;
                # большие вложения целиком с почтового сервера не скачиваем.
                status, raw = connection.uid(
                    "fetch", str(uid), "(BODY.PEEK[HEADER] BODY.PEEK[TEXT]<0.65536>)"
                )
                if status != "OK":
                    continue
                payload = b"".join(item[1] for item in raw if isinstance(item, tuple))
                if not payload:
                    continue
                parsed = BytesParser(policy=policy.default).parsebytes(payload)
                sender_name, sender_address = parseaddr(decode_text(parsed.get("From", "")))
                sender = sender_name or sender_address or "Неизвестный отправитель"
                if sender_name and sender_address:
                    sender = f"{sender_name} <{sender_address}>"
                preview, attachments = message_preview(parsed)
                messages.append(MailMessage(uid, sender, decode_text(parsed.get("Subject")) or "Без темы",
                                            preview, attachments))
            return uid_validity, latest_uid, messages
        finally:
            with suppress(Exception):
                connection.logout()


class MailNotifier:
    def __init__(self, db, bot: Bot, client: MailClient, interval=300):
        self.db = db
        self.bot = bot
        self.client = client
        self.interval = interval

    async def run(self):
        if not self.client.enabled:
            log.warning("MAIL_CREDENTIAL_KEY не задан: уведомления почты отключены")
            return
        while True:
            await self.check_all()
            await asyncio.sleep(self.interval)

    async def check_all(self):
        for account in self.db.mail_accounts():
            await self.check_account(account)

    async def check_account(self, account):
        previous_error = account["last_error"]
        try:
            password = self.client.decrypt(account["encrypted_password"])
            validity, latest_uid, messages = await asyncio.to_thread(
                self.client.new_messages, account["address"], password,
                account["uid_validity"], account["last_uid"],
            )
            changed_mailbox = (
                account["uid_validity"] is not None
                and validity != account["uid_validity"]
            )
            delivered_uid = latest_uid if changed_mailbox else account["last_uid"]
            for message in messages:
                text = (f"<b>Новое письмо на Бауманской почте</b>\n"
                        f"От: {escape(message.sender)}\n"
                        f"Тема: <b>{escape(message.subject)}</b>")
                if message.preview:
                    text += f"\n\n{escape(message.preview)}"
                if message.has_attachments:
                    text += "\n\nВ письме есть вложения."
                await self.bot.send_message(account["user_id"], text)
                delivered_uid = message.uid
            self.db.update_mail_cursor(account["user_id"], validity, delivered_uid)
        except Exception as error:
            error_text = str(error)
            self.db.set_mail_error(account["user_id"], error_text)
            log.warning("Mail check failed for user %s: %s", account["user_id"], error_text,
                        exc_info=not isinstance(error, (MailLoginError, ConnectionError, TelegramAPIError)))
            if error_text != previous_error:
                with suppress(TelegramAPIError):
                    await self.bot.send_message(
                        account["user_id"],
                        "Не удалось проверить Бауманскую почту. Открой профиль → «📨 Бауманская почта» "
                        "и переподключи аккаунт.",
                    )
