import hashlib
import secrets
import sqlite3
from contextlib import closing
from datetime import datetime
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
                role TEXT NOT NULL DEFAULT 'student',
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
        self.ensure_unique_numbers(path)
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(users)")}
        if "username" not in columns:
            with self.conn:
                self.conn.execute("ALTER TABLE users ADD COLUMN username TEXT")
        with self.conn:
            self.conn.execute("UPDATE users SET role='student' WHERE role='moderator'")

    def ensure_unique_numbers(self, path):
        duplicates = self.conn.execute("""
            SELECT journal_number FROM users
            WHERE journal_number IS NOT NULL
            GROUP BY journal_number HAVING COUNT(*) > 1
        """).fetchall()
        if duplicates:
            backup_path = path.with_name(f"{path.stem}.backup-{datetime.now():%Y%m%d-%H%M%S-%f}.sqlite3")
            with closing(sqlite3.connect(backup_path)) as backup:
                self.conn.backup(backup)
        with self.conn:
            for row in duplicates:
                self.conn.execute("UPDATE users SET journal_number=NULL WHERE journal_number=?", (row[0],))
            self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS unique_journal_number ON users(journal_number)")

    def group_members(self):
        return self.conn.execute("""
            SELECT * FROM users
            ORDER BY journal_number IS NULL, journal_number, name, telegram_id
        """).fetchall()

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
            self.conn.execute("INSERT INTO users(telegram_id, role) VALUES (?, 'student')", (telegram_id,))
        return True

    def set_name(self, telegram_id, name):
        with self.conn:
            self.conn.execute("UPDATE users SET name=? WHERE telegram_id=?", (name, telegram_id))

    def set_username(self, telegram_id, username):
        with self.conn:
            self.conn.execute("UPDATE users SET username=? WHERE telegram_id=?", (username, telegram_id))

    def set_number(self, telegram_id, number):
        try:
            with self.conn:
                self.conn.execute("UPDATE users SET journal_number=? WHERE telegram_id=?", (number, telegram_id))
        except sqlite3.IntegrityError:
            if number is not None and self.conn.execute(
                "SELECT 1 FROM users WHERE journal_number=? AND telegram_id<>?", (number, telegram_id)
            ).fetchone():
                return False
            raise
        return True

    def cache(self, group_uuid):
        return self.conn.execute("SELECT * FROM schedule_cache WHERE group_uuid=?", (group_uuid,)).fetchone()

    def save_cache(self, group_uuid, payload, updated_at):
        with self.conn:
            self.conn.execute("INSERT INTO schedule_cache VALUES (?,?,?) ON CONFLICT(group_uuid) "
                              "DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
                              (group_uuid, payload, updated_at))

    def close(self):
        self.conn.close()
