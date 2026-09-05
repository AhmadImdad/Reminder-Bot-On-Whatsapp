import sqlite3
from typing import List, Dict, Any, Optional
import json
import logging
import random
import string
from datetime import datetime, timedelta

import config

logger = logging.getLogger(__name__)

def get_db_connection() -> sqlite3.Connection:
    """Gets a connection to the SQLite database."""
    conn = sqlite3.connect(config.DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initializes the database schema."""
    logger.info("Initializing database...")
    with get_db_connection() as conn:
        cursor = conn.cursor()
        
        # Reminders table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_phone TEXT NOT NULL,
                task TEXT NOT NULL,
                reminder_datetime DATETIME NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at DATETIME NOT NULL,
                triggered_at DATETIME
            )
        ''')
        
        # Tasks table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_phone TEXT NOT NULL,
                task_name TEXT NOT NULL,
                end_datetime DATETIME,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at DATETIME NOT NULL
            )
        ''')
        
        # Messages table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_phone TEXT NOT NULL,
                message_type TEXT NOT NULL,
                message_content TEXT NOT NULL,
                timestamp DATETIME NOT NULL,
                processed BOOLEAN NOT NULL DEFAULT 0
            )
        ''')
        
        # Conversation state table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS conversation_state (
                user_phone TEXT PRIMARY KEY,
                state TEXT NOT NULL DEFAULT 'idle',
                context TEXT,
                updated_at DATETIME NOT NULL
            )
        ''')
        
        conn.commit()

        # Ideas table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ideas (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_phone TEXT NOT NULL,
                subject TEXT NOT NULL,
                description TEXT,
                media_type TEXT,
                media_path TEXT,
                media_original_name TEXT,
                created_at DATETIME NOT NULL
            )
        ''')

        # Notes table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_phone TEXT NOT NULL,
                subject TEXT NOT NULL,
                description TEXT,
                media_type TEXT,
                media_path TEXT,
                media_original_name TEXT,
                created_at DATETIME NOT NULL
            )
        ''')

        # Resources table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS resources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_phone TEXT NOT NULL,
                subject TEXT NOT NULL,
                description TEXT,
                media_type TEXT,
                media_path TEXT,
                media_original_name TEXT,
                created_at DATETIME NOT NULL
            )
        ''')

        # Dumps table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS dumps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_phone TEXT NOT NULL,
                subject TEXT NOT NULL,
                description TEXT,
                media_type TEXT,
                media_path TEXT,
                media_original_name TEXT,
                created_at DATETIME NOT NULL
            )
        ''')

        conn.commit()

        # Allowed users table — admin-managed whitelist
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS allowed_users (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                phone       TEXT NOT NULL UNIQUE,
                label       TEXT,
                is_admin    INTEGER DEFAULT 0,
                added_at    DATETIME NOT NULL
            )
        ''')

        # OTP tokens table — for forgot-password flow
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS otp_tokens (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                otp        TEXT NOT NULL,
                created_at DATETIME NOT NULL,
                expires_at DATETIME NOT NULL,
                used       INTEGER DEFAULT 0
            )
        ''')

        # Multi-media attachments — links extra files to any section entry
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS attachments (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                section       TEXT NOT NULL,
                entry_id      INTEGER NOT NULL,
                user_phone    TEXT NOT NULL,
                media_type    TEXT NOT NULL,
                media_path    TEXT NOT NULL,
                original_name TEXT,
                created_at    DATETIME NOT NULL
            )
        ''')

        # Temporary media staging — holds files while user decides where to save them
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS temp_media (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_phone    TEXT NOT NULL,
                file_path     TEXT NOT NULL,
                media_type    TEXT NOT NULL,
                original_name TEXT,
                saved_at      DATETIME NOT NULL,
                warning_sent  INTEGER DEFAULT 0,
                expires_at    DATETIME
            )
        ''')

        conn.commit()
    logger.info("Database initialized successfully.")


def log_message(user_phone: str, message_type: str, message_content: str) -> int:
    """Logs an incoming user message and returns its ID."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO messages (user_phone, message_type, message_content, timestamp, processed) VALUES (?, ?, ?, ?, ?)",
            (user_phone, message_type, message_content, datetime.utcnow(), False)
        )
        conn.commit()
        return cursor.lastrowid

def mark_message_processed(message_id: int):
    """Marks a message as processed."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE messages SET processed = 1 WHERE id = ?", (message_id,))
        conn.commit()

def get_conversation_state(user_phone: str) -> Dict[str, Any]:
    """Retrieves the conversation state for a user."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT state, context FROM conversation_state WHERE user_phone = ?", (user_phone,))
        row = cursor.fetchone()
        
        if row:
            return {
                "state": row["state"],
                "context": json.loads(row["context"]) if row["context"] else {}
            }
        
        # Default state
        return {"state": "idle", "context": {}}

def update_conversation_state(user_phone: str, state: str, context: Optional[Dict[str, Any]] = None):
    """Updates the conversation state for a user."""
    ctx_str = json.dumps(context) if context else "{}"
    now = datetime.utcnow()
    
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO conversation_state (user_phone, state, context, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_phone) DO UPDATE SET
            state=excluded.state,
            context=excluded.context,
            updated_at=excluded.updated_at
        ''', (user_phone, state, ctx_str, now))
        conn.commit()

def add_reminder(user_phone: str, task: str, reminder_datetime: datetime) -> int:
    """Adds a new reminder to the database."""
    now = datetime.utcnow()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO reminders (user_phone, task, reminder_datetime, status, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_phone, task, reminder_datetime, 'pending', now)
        )
        conn.commit()
        return cursor.lastrowid

def get_pending_reminders(until_datetime: datetime) -> List[sqlite3.Row]:
    """Gets all pending reminders up to a specific datetime."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM reminders WHERE status = 'pending' AND reminder_datetime <= ?",
            (until_datetime,)
        )
        return cursor.fetchall()
        
def get_user_pending_reminders(user_phone: str) -> List[sqlite3.Row]:
    """Gets all pending reminders for a specific user."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM reminders WHERE user_phone = ? AND status = 'pending' ORDER BY reminder_datetime ASC",
            (user_phone,)
        )
        return cursor.fetchall()

def mark_reminder_completed(reminder_id: int):
    """Marks a reminder as completed."""
    now = datetime.utcnow()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE reminders SET status = 'completed', triggered_at = ? WHERE id = ?",
            (now, reminder_id)
        )
        conn.commit()

def cancel_reminder(reminder_id: int, user_phone: str) -> bool:
    """Cancels a specific reminder. Returns True if successfully cancelled."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE reminders SET status = 'cancelled' WHERE id = ? AND user_phone = ? AND status = 'pending'",
            (reminder_id, user_phone)
        )
        conn.commit()
        return cursor.rowcount > 0

def delete_reminder(reminder_id: int, user_phone: str) -> bool:
    """Permanently deletes a reminder. Used for the undo feature."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM reminders WHERE id = ? AND user_phone = ?",
            (reminder_id, user_phone)
        )
        conn.commit()
        return cursor.rowcount > 0

def add_task(user_phone: str, task_name: str, end_datetime: Optional[datetime]) -> int:
    """Adds a new task to the database."""
    now = datetime.utcnow()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO tasks (user_phone, task_name, end_datetime, status, created_at) VALUES (?, ?, ?, ?, ?)",
            (user_phone, task_name, end_datetime, 'pending', now)
        )
        conn.commit()
        return cursor.lastrowid

def get_user_tasks(user_phone: str) -> List[sqlite3.Row]:
    """Gets all tasks for a specific user, ordered by creation."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM tasks WHERE user_phone = ? ORDER BY created_at ASC",
            (user_phone,)
        )
        return cursor.fetchall()

def mark_task_completed_by_offset(user_phone: str, offset_index: int) -> bool:
    """Marks a task as completed using its displayed list index (0-based offset)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM tasks WHERE user_phone = ? ORDER BY created_at ASC LIMIT 1 OFFSET ?",
            (user_phone, offset_index)
        )
        row = cursor.fetchone()
        if row:
            real_id = row['id']
            cursor.execute(
                "UPDATE tasks SET status = 'completed' WHERE id = ?",
                (real_id,)
            )
            conn.commit()
            return True
        return False

def delete_task_by_offset(user_phone: str, offset_index: int) -> bool:
    """Deletes a task using its displayed list index (0-based offset)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM tasks WHERE user_phone = ? ORDER BY created_at ASC LIMIT 1 OFFSET ?",
            (user_phone, offset_index)
        )
        row = cursor.fetchone()
        if row:
            real_id = row['id']
            cursor.execute(
                "DELETE FROM tasks WHERE id = ?",
                (real_id,)
            )
            conn.commit()
            return True
        return False

def delete_task(task_id: int, user_phone: str) -> bool:
    """Permanently deletes a task by its ID. Used for the undo feature."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM tasks WHERE id = ? AND user_phone = ?",
            (task_id, user_phone)
        )
        conn.commit()
        return cursor.rowcount > 0


# ──────────────────────────────────────────────────────────────────────────────
# IDEA STORE
# ──────────────────────────────────────────────────────────────────────────────

def add_idea(user_phone: str, subject: str, description: Optional[str],
             media_type: Optional[str] = None,
             media_path: Optional[str] = None,
             media_original_name: Optional[str] = None) -> int:
    """Saves a new idea and returns its assigned ID."""
    now = datetime.utcnow()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO ideas
                (user_phone, subject, description, media_type, media_path,
                 media_original_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_phone, subject, description, media_type, media_path,
             media_original_name, now)
        )
        conn.commit()
        return cursor.lastrowid


def get_ideas(user_phone: str) -> List[sqlite3.Row]:
    """Returns all ideas for a user, newest first."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM ideas WHERE user_phone = ? ORDER BY created_at DESC",
            (user_phone,)
        )
        return cursor.fetchall()


def get_idea_by_id(idea_id: int, user_phone: str) -> Optional[sqlite3.Row]:
    """Returns a single idea by ID, scoped to the requesting user.
    A user can never retrieve another user's idea even if they guess the ID."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM ideas WHERE id = ? AND user_phone = ?",
            (idea_id, user_phone)
        )
        return cursor.fetchone()


def delete_idea(idea_id: int, user_phone: str) -> bool:
    """Deletes an idea. Returns True only if a row was actually deleted.
    Scoped to user_phone so users cannot delete each other's ideas."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM ideas WHERE id = ? AND user_phone = ?",
            (idea_id, user_phone)
        )
        conn.commit()
        return cursor.rowcount > 0


# ──────────────────────────────────────────────────────────────────────────────
# NOTES STORE
# ──────────────────────────────────────────────────────────────────────────────

def add_note(user_phone: str, subject: str, description: Optional[str],
             media_type: Optional[str] = None,
             media_path: Optional[str] = None,
             media_original_name: Optional[str] = None) -> int:
    """Saves a new note and returns its assigned ID."""
    now = datetime.utcnow()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO notes
                (user_phone, subject, description, media_type, media_path,
                 media_original_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_phone, subject, description, media_type, media_path,
             media_original_name, now)
        )
        conn.commit()
        return cursor.lastrowid


def get_notes(user_phone: str) -> List[sqlite3.Row]:
    """Returns all notes for a user, newest first."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM notes WHERE user_phone = ? ORDER BY created_at DESC",
            (user_phone,)
        )
        return cursor.fetchall()


def get_note_by_id(note_id: int, user_phone: str) -> Optional[sqlite3.Row]:
    """Returns a single note by ID, scoped to the requesting user."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM notes WHERE id = ? AND user_phone = ?",
            (note_id, user_phone)
        )
        return cursor.fetchone()


def delete_note(note_id: int, user_phone: str) -> bool:
    """Deletes a note. Returns True only if a row was actually deleted."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM notes WHERE id = ? AND user_phone = ?",
            (note_id, user_phone)
        )
        conn.commit()
        return cursor.rowcount > 0


# ──────────────────────────────────────────────────────────────────────────────
# RESOURCES STORE
# ──────────────────────────────────────────────────────────────────────────────

def add_resource(user_phone: str, subject: str, description: Optional[str],
                 media_type: Optional[str] = None,
                 media_path: Optional[str] = None,
                 media_original_name: Optional[str] = None) -> int:
    """Saves a new resource and returns its assigned ID."""
    now = datetime.utcnow()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO resources
                (user_phone, subject, description, media_type, media_path,
                 media_original_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_phone, subject, description, media_type, media_path,
             media_original_name, now)
        )
        conn.commit()
        return cursor.lastrowid

def get_resources(user_phone: str) -> List[sqlite3.Row]:
    """Returns all resources for a user, newest first."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM resources WHERE user_phone = ? ORDER BY created_at DESC",
            (user_phone,)
        )
        return cursor.fetchall()

def get_resource_by_id(resource_id: int, user_phone: str) -> Optional[sqlite3.Row]:
    """Returns a single resource by ID, scoped to the requesting user."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM resources WHERE id = ? AND user_phone = ?",
            (resource_id, user_phone)
        )
        return cursor.fetchone()

def delete_resource(resource_id: int, user_phone: str) -> bool:
    """Deletes a resource. Returns True only if a row was actually deleted."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM resources WHERE id = ? AND user_phone = ?",
            (resource_id, user_phone)
        )
        conn.commit()
        return cursor.rowcount > 0


# ──────────────────────────────────────────────────────────────────────────────
# DUMP STORE
# ──────────────────────────────────────────────────────────────────────────────

def add_dump(user_phone: str, subject: str, description: Optional[str],
             media_type: Optional[str] = None,
             media_path: Optional[str] = None,
             media_original_name: Optional[str] = None) -> int:
    """Saves a new dump and returns its assigned ID."""
    now = datetime.utcnow()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO dumps
                (user_phone, subject, description, media_type, media_path,
                 media_original_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (user_phone, subject, description, media_type, media_path,
             media_original_name, now)
        )
        conn.commit()
        return cursor.lastrowid

def get_dumps(user_phone: str) -> List[sqlite3.Row]:
    """Returns all dumps for a user, newest first."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM dumps WHERE user_phone = ? ORDER BY created_at DESC",
            (user_phone,)
        )
        return cursor.fetchall()

def get_dump_by_id(dump_id: int, user_phone: str) -> Optional[sqlite3.Row]:
    """Returns a single dump by ID, scoped to the requesting user."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM dumps WHERE id = ? AND user_phone = ?",
            (dump_id, user_phone)
        )
        return cursor.fetchone()

def delete_dump(dump_id: int, user_phone: str) -> bool:
    """Deletes a dump. Returns True only if a row was actually deleted."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM dumps WHERE id = ? AND user_phone = ?",
            (dump_id, user_phone)
        )
        conn.commit()
        return cursor.rowcount > 0


# ──────────────────────────────────────────────────────────────────────────────
# ALLOWED USERS (Multi-user access control)
# ──────────────────────────────────────────────────────────────────────────────

def seed_admin_phone() -> None:
    """
    Ensures the admin phone number from config is always present in allowed_users.
    Called once at startup. Safe to call multiple times (uses INSERT OR IGNORE).
    """
    admin_phone = config.ALLOWED_PHONE_NUMBER
    if not admin_phone:
        return
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR IGNORE INTO allowed_users (phone, label, is_admin, added_at)
            VALUES (?, ?, 1, ?)
            """,
            (admin_phone, "Admin", datetime.utcnow())
        )
        conn.commit()
    logger.info(f"Admin phone seeded: {admin_phone}")


def is_phone_allowed(phone: str) -> bool:
    """Returns True if the given phone number is in the allowed_users table."""
    phone_clean = phone.split("@")[0]
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT 1 FROM allowed_users WHERE phone = ?",
            (phone_clean,)
        )
        return cursor.fetchone() is not None


def get_allowed_phones() -> List[str]:
    """Returns a list of all allowed phone number strings."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT phone FROM allowed_users ORDER BY added_at ASC")
        return [row["phone"] for row in cursor.fetchall()]


def get_all_allowed_users() -> List[sqlite3.Row]:
    """Returns all rows from allowed_users for the admin UI."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM allowed_users ORDER BY is_admin DESC, added_at ASC")
        return cursor.fetchall()


def add_allowed_user(phone: str, label: str = "") -> bool:
    """Adds a new allowed user. Returns True if inserted, False if already exists."""
    phone_clean = phone.strip()
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT INTO allowed_users (phone, label, is_admin, added_at)
                VALUES (?, ?, 0, ?)
                """,
                (phone_clean, label.strip(), datetime.utcnow())
            )
            conn.commit()
            return True
    except sqlite3.IntegrityError:
        # UNIQUE constraint — user already exists
        return False


def remove_allowed_user(phone: str) -> bool:
    """
    Removes an allowed user and CASCADES deletion of ALL their data:
    reminders, tasks, notes, ideas, resources, dumps, messages, conversation_state.
    Admin phone (is_admin=1) cannot be removed.
    Returns True if a user was removed.
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()

        # Safety: never remove the admin
        cursor.execute(
            "SELECT is_admin FROM allowed_users WHERE phone = ?",
            (phone,)
        )
        row = cursor.fetchone()
        if not row or row["is_admin"] == 1:
            logger.warning(f"Attempted to remove admin or non-existent user: {phone}")
            return False

        # Cascade delete all user data
        tables = [
            "reminders", "tasks", "notes", "ideas",
            "resources", "dumps", "messages", "conversation_state"
        ]
        for table in tables:
            col = "user_phone" if table != "conversation_state" else "user_phone"
            cursor.execute(f"DELETE FROM {table} WHERE {col} = ?", (phone,))

        # Remove from allowed list
        cursor.execute("DELETE FROM allowed_users WHERE phone = ?", (phone,))
        conn.commit()
        logger.info(f"User {phone} and all associated data removed.")
        return True


def is_admin_phone(phone: str) -> bool:
    """Returns True if the given phone is the admin."""
    phone_clean = phone.split("@")[0]
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT is_admin FROM allowed_users WHERE phone = ?",
            (phone_clean,)
        )
        row = cursor.fetchone()
        return bool(row and row["is_admin"] == 1)


# ──────────────────────────────────────────────────────────────────────────────
# OTP TOKENS (Forgot-password flow)
# ──────────────────────────────────────────────────────────────────────────────

def generate_otp(length: int = 6) -> str:
    """Generates a cryptographically random numeric OTP."""
    return "".join(random.choices(string.digits, k=length))


def create_otp() -> str:
    """
    Generates a new 6-digit OTP, stores it in the DB with a 10-minute expiry,
    invalidates any previous unused OTPs, and returns the OTP string.
    """
    otp = generate_otp()
    now = datetime.utcnow()
    expires_at = now + timedelta(minutes=10)

    with get_db_connection() as conn:
        cursor = conn.cursor()
        # Invalidate previous OTPs
        cursor.execute("UPDATE otp_tokens SET used = 1 WHERE used = 0")
        # Insert new OTP
        cursor.execute(
            """
            INSERT INTO otp_tokens (otp, created_at, expires_at, used)
            VALUES (?, ?, ?, 0)
            """,
            (otp, now, expires_at)
        )
        conn.commit()
    return otp


def verify_otp(otp: str) -> bool:
    """
    Verifies an OTP: must exist, be unused, and not expired.
    Marks it as used on success.
    Returns True if valid.
    """
    now = datetime.utcnow()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT id FROM otp_tokens
            WHERE otp = ? AND used = 0 AND expires_at > ?
            """,
            (otp, now)
        )
        row = cursor.fetchone()
        if row:
            cursor.execute(
                "UPDATE otp_tokens SET used = 1 WHERE id = ?",
                (row["id"],)
            )
            conn.commit()
            return True
        return False


# ──────────────────────────────────────────────────────────────────────────────
# ATTACHMENTS (multi-media per entry)
# ──────────────────────────────────────────────────────────────────────────────

def add_attachment(section: str, entry_id: int, user_phone: str,
                   media_type: str, media_path: str,
                   original_name: Optional[str] = None) -> int:
    """Links an extra media file to an existing section entry."""
    now = datetime.utcnow()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO attachments (section, entry_id, user_phone, media_type,
                                     media_path, original_name, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (section, entry_id, user_phone, media_type, media_path, original_name, now)
        )
        conn.commit()
        return cursor.lastrowid


def get_attachments(section: str, entry_id: int, user_phone: str) -> List[sqlite3.Row]:
    """Returns all extra attachments for a given section entry."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM attachments
            WHERE section = ? AND entry_id = ? AND user_phone = ?
            ORDER BY created_at ASC
            """,
            (section, entry_id, user_phone)
        )
        return cursor.fetchall()


def delete_attachments_for_entry(section: str, entry_id: int, user_phone: str) -> int:
    """Deletes all attachments for a given entry. Returns count deleted."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM attachments WHERE section = ? AND entry_id = ? AND user_phone = ?",
            (section, entry_id, user_phone)
        )
        conn.commit()
        return cursor.rowcount


# ──────────────────────────────────────────────────────────────────────────────
# TEMP MEDIA (staging area for unassigned files)
# ──────────────────────────────────────────────────────────────────────────────

def add_temp_media(user_phone: str, file_path: str,
                   media_type: str, original_name: Optional[str] = None) -> int:
    """Saves a new temp_media row and returns its ID."""
    now = datetime.utcnow()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO temp_media (user_phone, file_path, media_type, original_name,
                                    saved_at, warning_sent, expires_at)
            VALUES (?, ?, ?, ?, ?, 0, NULL)
            """,
            (user_phone, file_path, media_type, original_name, now)
        )
        conn.commit()
        return cursor.lastrowid


def get_pending_temp_media(user_phone: str) -> Optional[sqlite3.Row]:
    """Returns the most recent unsettled temp_media row for a user, or None."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM temp_media
            WHERE user_phone = ? AND warning_sent = 0
            ORDER BY saved_at DESC LIMIT 1
            """,
            (user_phone,)
        )
        return cursor.fetchone()


def get_all_pending_temp_media(user_phone: str) -> List[sqlite3.Row]:
    """Returns all unsettled temp_media rows for a user."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM temp_media WHERE user_phone = ? ORDER BY saved_at ASC",
            (user_phone,)
        )
        return cursor.fetchall()


def get_expiring_temp_media() -> List[sqlite3.Row]:
    """Returns rows that are >60 min old and warning not yet sent."""
    threshold = datetime.utcnow() - timedelta(minutes=60)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM temp_media WHERE saved_at <= ? AND warning_sent = 0",
            (threshold,)
        )
        return cursor.fetchall()


def get_expired_temp_media() -> List[sqlite3.Row]:
    """Returns rows whose expires_at has passed (warning was sent, user didn't respond)."""
    now = datetime.utcnow()
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM temp_media WHERE warning_sent = 1 AND expires_at IS NOT NULL AND expires_at <= ?",
            (now,)
        )
        return cursor.fetchall()


def mark_temp_media_warning_sent(row_id: int) -> None:
    """Marks warning as sent and sets a 10-minute expiry window."""
    expires_at = datetime.utcnow() + timedelta(minutes=10)
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE temp_media SET warning_sent = 1, expires_at = ? WHERE id = ?",
            (expires_at, row_id)
        )
        conn.commit()


def delete_temp_media(row_id: int) -> None:
    """Removes a temp_media row from the DB (caller must also delete the file)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM temp_media WHERE id = ?", (row_id,))
        conn.commit()


def get_temp_media_by_id(row_id: int, user_phone: str) -> Optional[sqlite3.Row]:
    """Fetches a specific temp_media row scoped to a user."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM temp_media WHERE id = ? AND user_phone = ?",
            (row_id, user_phone)
        )
        return cursor.fetchone()
