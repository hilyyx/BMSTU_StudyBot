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

    @classmethod
    def load(cls):
        load_dotenv()
        token = os.getenv("BOT_TOKEN", "").strip()
        owner = os.getenv("OWNER_ID", "").strip()
        if not token or not owner.isascii() or not owner.isdigit() or int(owner) <= 0:
            raise ValueError("Заполните BOT_TOKEN и положительный OWNER_ID в .env")
        return cls(
            token=token,
            owner_id=int(owner),
            database=Path(os.getenv("DATABASE_PATH", "data/bot.sqlite3")),
            group_uuid=os.getenv("GROUP_UUID", "ab0c54d0-1624-11f1-9d39-005056aae53e"),
        )
