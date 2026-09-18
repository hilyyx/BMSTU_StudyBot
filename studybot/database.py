import hashlib
import json
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
            CREATE TABLE IF NOT EXISTS homework (
                id INTEGER PRIMARY KEY, subject TEXT NOT NULL, description TEXT NOT NULL,
                attachments TEXT NOT NULL, due_at TEXT NOT NULL, lesson_type TEXT NOT NULL,
                delivery TEXT NOT NULL, author_id INTEGER NOT NULL, deleted INTEGER NOT NULL DEFAULT 0,
                version INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS homework_done (
                homework_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
                PRIMARY KEY(homework_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS homework_drafts (
                user_id INTEGER PRIMARY KEY, payload TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS homework_history (
                id INTEGER PRIMARY KEY, homework_id INTEGER NOT NULL, editor_id INTEGER NOT NULL,
                action TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
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

    def draft(self, user_id):
        row = self.conn.execute("SELECT payload FROM homework_drafts WHERE user_id=?", (user_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_draft(self, user_id, draft):
        with self.conn:
            self.conn.execute("INSERT INTO homework_drafts VALUES (?,?) ON CONFLICT(user_id) "
                              "DO UPDATE SET payload=excluded.payload", (user_id, json.dumps(draft, ensure_ascii=False)))

    def clear_draft(self, user_id):
        with self.conn:
            self.conn.execute("DELETE FROM homework_drafts WHERE user_id=?", (user_id,))

    def homework(self, homework_id, user_id):
        return self.conn.execute("""
            SELECT h.*, u.name AS author_name,
                EXISTS(SELECT 1 FROM homework_done d WHERE d.homework_id=h.id AND d.user_id=?) AS done
            FROM homework h LEFT JOIN users u ON u.telegram_id=h.author_id WHERE h.id=?
        """, (user_id, homework_id)).fetchone()

    def homework_subjects(self):
        return [row[0] for row in self.conn.execute("SELECT DISTINCT subject FROM homework WHERE deleted=0 ORDER BY subject")]

    def homework_list(self, user_id, subject=None, overdue_at=None, deleted=False):
        query = """SELECT h.*, u.name AS author_name,
            EXISTS(SELECT 1 FROM homework_done d WHERE d.homework_id=h.id AND d.user_id=?) AS done
            FROM homework h LEFT JOIN users u ON u.telegram_id=h.author_id WHERE h.deleted=?"""
        params = [user_id, int(deleted)]
        if subject:
            query += " AND h.subject=?"
            params.append(subject)
        if overdue_at:
            query += " AND h.due_at<? AND NOT EXISTS(SELECT 1 FROM homework_done d WHERE d.homework_id=h.id AND d.user_id=?)"
            params.extend([overdue_at, user_id])
        return self.conn.execute(query + " ORDER BY h.due_at,h.id", params).fetchall()

    def publish_homework(self, user_id, owner_id, token):
        with self.conn:
            draft = self.draft(user_id)
            if not draft or draft.get("step") != "confirm" or draft["token"] != token:
                return None
            values = (draft["subject"], draft["description"], json.dumps(draft["attachments"]),
                      draft["due_at"], draft["lesson_type"], draft["delivery"])
            homework_id = draft.get("edit_id")
            if homework_id:
                cursor = self.conn.execute("""UPDATE homework SET subject=?,description=?,attachments=?,due_at=?,
                    lesson_type=?,delivery=?,version=version+1 WHERE id=? AND version=? AND deleted=0
                    AND (author_id=? OR ?=?)""", values + (homework_id, draft["version"], user_id, user_id, owner_id))
                if cursor.rowcount != 1:
                    return None
            else:
                cursor = self.conn.execute("""INSERT INTO homework(subject,description,attachments,due_at,lesson_type,
                    delivery,author_id) VALUES (?,?,?,?,?,?,?)""", values + (user_id,))
                homework_id = cursor.lastrowid
            self.conn.execute("INSERT INTO homework_history(homework_id,editor_id,action,payload) VALUES (?,?,?,?)",
                              (homework_id, user_id, "edit" if draft.get("edit_id") else "create", json.dumps(draft)))
            self.conn.execute("DELETE FROM homework_drafts WHERE user_id=?", (user_id,))
        return homework_id

    def set_homework_deleted(self, homework_id, user_id, owner_id, deleted):
        with self.conn:
            cursor = self.conn.execute("UPDATE homework SET deleted=?,version=version+1 WHERE id=? "
                                       "AND deleted<>? AND (author_id=? OR ?=?)",
                                       (int(deleted), homework_id, int(deleted), user_id, user_id, owner_id))
            if cursor.rowcount != 1:
                return False
            self.conn.execute("INSERT INTO homework_history(homework_id,editor_id,action,payload) VALUES (?,?,?,?)",
                              (homework_id, user_id, "delete" if deleted else "restore", "{}"))
        return True

    def toggle_homework_done(self, homework_id, user_id):
        with self.conn:
            item = self.homework(homework_id, user_id)
            if not item or item["deleted"]:
                return False
            if item["done"]:
                self.conn.execute("DELETE FROM homework_done WHERE homework_id=? AND user_id=?", (homework_id, user_id))
            else:
                self.conn.execute("INSERT INTO homework_done VALUES (?,?)", (homework_id, user_id))
        return True
