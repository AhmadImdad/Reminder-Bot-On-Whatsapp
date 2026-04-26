import sqlite3
from typing import List, Dict, Any, Optional
import json
import logging
from datetime import datetime

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
