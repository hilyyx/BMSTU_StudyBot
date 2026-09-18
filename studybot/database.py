import hashlib
import secrets
import sqlite3
from pathlib import Path


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                name TEXT,
                journal_number INTEGER CHECK(journal_number BETWEEN 1 AND 30),
                role TEXT NOT NULL DEFAULT 'moderator',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS invitations (
                code_hash TEXT PRIMARY KEY,
                used_by INTEGER UNIQUE,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS schedule_cache (
                group_uuid TEXT PRIMARY KEY, payload TEXT NOT NULL, updated_at TEXT NOT NULL
            );
        """)

    def user(self, telegram_id):
        return self.conn.execute("SELECT * FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()

    def add_owner(self, telegram_id):
        with self.conn:
            self.conn.execute("INSERT INTO users(telegram_id,role) VALUES (?, 'owner') "
                              "ON CONFLICT(telegram_id) DO UPDATE SET role='owner'", (telegram_id,))

    def invitations(self, count):
        codes = [secrets.token_urlsafe(12) for _ in range(count)]
        with self.conn:
            self.conn.executemany("INSERT INTO invitations(code_hash) VALUES (?)",
                                  [(self.hash(code),) for code in codes])
        return codes

    @staticmethod
    def hash(code):
        return hashlib.sha256(code.encode()).hexdigest()

    def redeem(self, code, telegram_id):
        with self.conn:
            if self.user(telegram_id):
                return False
            cursor = self.conn.execute("UPDATE invitations SET used_by=? WHERE code_hash=? AND used_by IS NULL",
                                       (telegram_id, self.hash(code)))
            if cursor.rowcount != 1:
                return False
            self.conn.execute("INSERT INTO users(telegram_id) VALUES (?)", (telegram_id,))
        return True

    def set_name(self, telegram_id, name):
        with self.conn:
            self.conn.execute("UPDATE users SET name=? WHERE telegram_id=?", (name, telegram_id))

    def set_number(self, telegram_id, number):
        with self.conn:
            self.conn.execute("UPDATE users SET journal_number=? WHERE telegram_id=?", (number, telegram_id))

    def cache(self, group_uuid):
        return self.conn.execute("SELECT * FROM schedule_cache WHERE group_uuid=?", (group_uuid,)).fetchone()

    def save_cache(self, group_uuid, payload, updated_at):
        with self.conn:
            self.conn.execute("INSERT INTO schedule_cache VALUES (?,?,?) ON CONFLICT(group_uuid) "
                              "DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
                              (group_uuid, payload, updated_at))

    def close(self):
        self.conn.close()
