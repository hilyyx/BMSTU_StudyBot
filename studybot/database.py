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
            CREATE TABLE IF NOT EXISTS profile_edits (
                user_id INTEGER PRIMARY KEY, name TEXT
            );
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, text TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS feedback_inputs (
                user_id INTEGER PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS mail_accounts (
                user_id INTEGER PRIMARY KEY,
                address TEXT NOT NULL UNIQUE,
                encrypted_password TEXT NOT NULL,
                uid_validity INTEGER,
                last_uid INTEGER NOT NULL DEFAULT 0,
                last_checked_at TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS mail_setups (
                user_id INTEGER PRIMARY KEY,
                address TEXT
            );
        """)
        self.ensure_unique_numbers(path)
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(users)")}
        if "username" not in columns:
            with self.conn:
                self.conn.execute("ALTER TABLE users ADD COLUMN username TEXT")
        with self.conn:
            self.conn.execute("UPDATE users SET role='student' WHERE role='moderator'")
        columns = {row["name"] for row in self.conn.execute("PRAGMA table_info(homework)")}
        with self.conn:
            for name, definition in (("kind", "TEXT NOT NULL DEFAULT 'regular'"),
                                     ("title", "TEXT NOT NULL DEFAULT ''"), ("due_week", "INTEGER")):
                if name not in columns:
                    self.conn.execute(f"ALTER TABLE homework ADD COLUMN {name} {definition}")
            deleted_ids = [row[0] for row in self.conn.execute("SELECT id FROM homework WHERE deleted=1")]
            if deleted_ids:
                placeholders = ",".join("?" for _ in deleted_ids)
                self.conn.execute(f"DELETE FROM homework_done WHERE homework_id IN ({placeholders})", deleted_ids)
                self.conn.execute(f"DELETE FROM homework_history WHERE homework_id IN ({placeholders})", deleted_ids)
                self.conn.execute(f"DELETE FROM homework WHERE id IN ({placeholders})", deleted_ids)

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

    def remove_user(self, telegram_id, owner_id):
        if telegram_id == owner_id or not self.user(telegram_id):
            return False
        with self.conn:
            # Удалённый участник сможет вернуться только по новому приглашению.
            self.conn.execute("DELETE FROM invitations WHERE used_by=?", (telegram_id,))
            self.conn.execute("DELETE FROM homework_done WHERE user_id=?", (telegram_id,))
            self.conn.execute("DELETE FROM homework_drafts WHERE user_id=?", (telegram_id,))
            self.conn.execute("DELETE FROM profile_edits WHERE user_id=?", (telegram_id,))
            self.conn.execute("DELETE FROM feedback_inputs WHERE user_id=?", (telegram_id,))
            self.conn.execute("DELETE FROM mail_setups WHERE user_id=?", (telegram_id,))
            self.conn.execute("DELETE FROM mail_accounts WHERE user_id=?", (telegram_id,))
            self.conn.execute("DELETE FROM users WHERE telegram_id=?", (telegram_id,))
        return True

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

    def start_profile_edit(self, user_id):
        with self.conn:
            self.conn.execute("INSERT INTO profile_edits(user_id) VALUES (?) "
                              "ON CONFLICT(user_id) DO UPDATE SET name=NULL", (user_id,))

    def profile_edit(self, user_id):
        return self.conn.execute("SELECT * FROM profile_edits WHERE user_id=?", (user_id,)).fetchone()

    def set_profile_edit_name(self, user_id, name):
        with self.conn:
            self.conn.execute("UPDATE profile_edits SET name=? WHERE user_id=?", (name, user_id))

    def cancel_profile_edit(self, user_id):
        with self.conn:
            self.conn.execute("DELETE FROM profile_edits WHERE user_id=?", (user_id,))

    def finish_profile_edit(self, user_id, number):
        edit = self.profile_edit(user_id)
        if not edit or not edit["name"]:
            return False
        try:
            with self.conn:
                self.conn.execute("UPDATE users SET name=?,journal_number=? WHERE telegram_id=?",
                                  (edit["name"], number, user_id))
                self.conn.execute("DELETE FROM profile_edits WHERE user_id=?", (user_id,))
        except sqlite3.IntegrityError:
            if self.conn.execute("SELECT 1 FROM users WHERE journal_number=? AND telegram_id<>?",
                                 (number, user_id)).fetchone():
                return False
            raise
        return True

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

    def homework_subjects(self, kind=None):
        query = "SELECT DISTINCT subject FROM homework WHERE deleted=0"
        params = ()
        if kind:
            query += " AND kind=?"
            params = (kind,)
        return [row[0] for row in self.conn.execute(query + " ORDER BY subject", params)]

    def homework_list(self, user_id, subject=None, overdue_at=None, due_from=None,
                      deleted=False, kind=None, done=None):
        query = """SELECT h.*, u.name AS author_name,
            EXISTS(SELECT 1 FROM homework_done d WHERE d.homework_id=h.id AND d.user_id=?) AS done
            FROM homework h LEFT JOIN users u ON u.telegram_id=h.author_id WHERE h.deleted=?"""
        params = [user_id, int(deleted)]
        if kind:
            query += " AND h.kind=?"
            params.append(kind)
        if subject:
            query += " AND h.subject=?"
            params.append(subject)
        if done is not None:
            query += " AND " + ("" if done else "NOT ") + "EXISTS(SELECT 1 FROM homework_done d WHERE d.homework_id=h.id AND d.user_id=?)"
            params.append(user_id)
        if overdue_at:
            query += " AND h.due_at<? AND NOT EXISTS(SELECT 1 FROM homework_done d WHERE d.homework_id=h.id AND d.user_id=?)"
            params.extend([overdue_at, user_id])
        if due_from:
            query += " AND h.due_at>=?"
            params.append(due_from)
        return self.conn.execute(query + " ORDER BY h.due_at,h.id", params).fetchall()

    def publish_homework(self, user_id, owner_id, token):
        with self.conn:
            draft = self.draft(user_id)
            if not draft or draft.get("step") != "confirm" or draft["token"] != token:
                return None
            values = (draft["subject"], draft["description"], json.dumps(draft["attachments"]),
                      draft["due_at"], draft["lesson_type"], draft["delivery"],
                      draft.get("kind", "regular"), draft.get("title", ""), draft.get("due_week"))
            homework_id = draft.get("edit_id")
            if homework_id:
                cursor = self.conn.execute("""UPDATE homework SET subject=?,description=?,attachments=?,due_at=?,
                    lesson_type=?,delivery=?,kind=?,title=?,due_week=?,version=version+1 WHERE id=? AND version=? AND deleted=0
                    AND (author_id=? OR ?=?)""", values + (homework_id, draft["version"], user_id, user_id, owner_id))
                if cursor.rowcount != 1:
                    return None
            else:
                cursor = self.conn.execute("""INSERT INTO homework(subject,description,attachments,due_at,lesson_type,
                    delivery,kind,title,due_week,author_id) VALUES (?,?,?,?,?,?,?,?,?,?)""", values + (user_id,))
                homework_id = cursor.lastrowid
            self.conn.execute("INSERT INTO homework_history(homework_id,editor_id,action,payload) VALUES (?,?,?,?)",
                              (homework_id, user_id, "edit" if draft.get("edit_id") else "create", json.dumps(draft)))
            self.conn.execute("DELETE FROM homework_drafts WHERE user_id=?", (user_id,))
        return homework_id

    def delete_homework(self, homework_id, user_id, owner_id):
        with self.conn:
            item = self.conn.execute("SELECT author_id FROM homework WHERE id=?", (homework_id,)).fetchone()
            if not item or (item["author_id"] != user_id and user_id != owner_id):
                return False
            self.conn.execute("DELETE FROM homework_done WHERE homework_id=?", (homework_id,))
            self.conn.execute("DELETE FROM homework_history WHERE homework_id=?", (homework_id,))
            self.conn.execute("DELETE FROM homework WHERE id=?", (homework_id,))
        return True

    def start_feedback(self, user_id):
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO feedback_inputs(user_id) VALUES (?)", (user_id,))

    def feedback_pending(self, user_id):
        return self.conn.execute("SELECT 1 FROM feedback_inputs WHERE user_id=?", (user_id,)).fetchone() is not None

    def cancel_feedback(self, user_id):
        with self.conn:
            self.conn.execute("DELETE FROM feedback_inputs WHERE user_id=?", (user_id,))

    def add_feedback(self, user_id, text):
        with self.conn:
            cursor = self.conn.execute("INSERT INTO feedback(user_id,text) VALUES (?,?)", (user_id, text))
            self.conn.execute("DELETE FROM feedback_inputs WHERE user_id=?", (user_id,))
        return cursor.lastrowid

    def feedback_list(self, limit=30):
        return self.conn.execute("""
            SELECT f.*,u.name,u.username,u.journal_number
            FROM feedback f LEFT JOIN users u ON u.telegram_id=f.user_id
            ORDER BY f.id DESC LIMIT ?
        """, (limit,)).fetchall()

    def mail_account(self, user_id):
        return self.conn.execute("SELECT * FROM mail_accounts WHERE user_id=?", (user_id,)).fetchone()

    def mail_accounts(self):
        return self.conn.execute("SELECT * FROM mail_accounts ORDER BY user_id").fetchall()

    def start_mail_setup(self, user_id):
        with self.conn:
            self.conn.execute("INSERT INTO mail_setups(user_id) VALUES (?) "
                              "ON CONFLICT(user_id) DO UPDATE SET address=NULL", (user_id,))

    def mail_setup(self, user_id):
        return self.conn.execute("SELECT * FROM mail_setups WHERE user_id=?", (user_id,)).fetchone()

    def set_mail_setup_address(self, user_id, address):
        with self.conn:
            self.conn.execute("UPDATE mail_setups SET address=? WHERE user_id=?", (address, user_id))

    def cancel_mail_setup(self, user_id):
        with self.conn:
            self.conn.execute("DELETE FROM mail_setups WHERE user_id=?", (user_id,))

    def save_mail_account(self, user_id, address, encrypted_password, uid_validity, last_uid):
        try:
            with self.conn:
                self.conn.execute("""
                    INSERT INTO mail_accounts(user_id,address,encrypted_password,uid_validity,last_uid,last_error)
                    VALUES (?,?,?,?,?,NULL)
                    ON CONFLICT(user_id) DO UPDATE SET address=excluded.address,
                        encrypted_password=excluded.encrypted_password,
                        uid_validity=excluded.uid_validity,last_uid=excluded.last_uid,
                        last_checked_at=NULL,last_error=NULL
                """, (user_id, address, encrypted_password, uid_validity, last_uid))
                self.conn.execute("DELETE FROM mail_setups WHERE user_id=?", (user_id,))
        except sqlite3.IntegrityError:
            return False
        return True

    def update_mail_cursor(self, user_id, uid_validity, last_uid):
        with self.conn:
            self.conn.execute("""UPDATE mail_accounts SET uid_validity=?,last_uid=?,
                last_checked_at=CURRENT_TIMESTAMP,last_error=NULL WHERE user_id=?""",
                              (uid_validity, last_uid, user_id))

    def set_mail_error(self, user_id, error):
        with self.conn:
            self.conn.execute("""UPDATE mail_accounts SET last_checked_at=CURRENT_TIMESTAMP,last_error=?
                WHERE user_id=?""", (error[:300], user_id))

    def delete_mail_account(self, user_id):
        with self.conn:
            self.conn.execute("DELETE FROM mail_setups WHERE user_id=?", (user_id,))
            return self.conn.execute("DELETE FROM mail_accounts WHERE user_id=?", (user_id,)).rowcount == 1

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
