import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    token: str
    owner_id: int
    database: Path
    group_uuid: str
    mail_key: str = ""
    mail_host: str = "mail.bmstu.ru"
    mail_port: int = 993
    mail_check_interval: int = 300

    @classmethod
    def load(cls):
        load_dotenv()
        token = os.getenv("BOT_TOKEN", "").strip()
        owner = os.getenv("OWNER_ID", "").strip()
        if not token or not owner.isascii() or not owner.isdigit() or int(owner) <= 0:
            raise ValueError("Заполните BOT_TOKEN и положительный OWNER_ID в .env")
        mail_port = cls.positive_int("MAIL_PORT", 993)
        mail_check_interval = cls.positive_int("MAIL_CHECK_INTERVAL", 300)
        return cls(
            token=token,
            owner_id=int(owner),
            database=Path(os.getenv("DATABASE_PATH", "data/bot.sqlite3")),
            group_uuid=os.getenv("GROUP_UUID", "ab0c54d0-1624-11f1-9d39-005056aae53e"),
            mail_key=os.getenv("MAIL_CREDENTIAL_KEY", "").strip(),
            mail_host=os.getenv("MAIL_HOST", "mail.bmstu.ru").strip(),
            mail_port=mail_port,
            mail_check_interval=mail_check_interval,
        )

    @staticmethod
    def positive_int(name, default):
        value = os.getenv(name, str(default)).strip()
        if not value.isascii() or not value.isdigit() or int(value) <= 0:
            raise ValueError(f"{name} должен быть положительным целым числом")
        return int(value)
