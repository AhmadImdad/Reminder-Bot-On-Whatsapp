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
