import sqlite3
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
import config_dashboard

def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(config_dashboard.DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES)
    conn.row_factory = sqlite3.Row
    return conn

def get_pending_reminders(user_phone: Optional[str] = None) -> pd.DataFrame:
    with get_db_connection() as conn:
        query = "SELECT * FROM reminders WHERE status = 'pending'"
        params = []
        if user_phone:
            query += " AND user_phone = ?"
            params.append(user_phone)
        query += " ORDER BY reminder_datetime ASC"
        df = pd.read_sql_query(query, conn, params=params)
        
    # Standardize datetime parsing and convert to local timezone
    if not df.empty:
        import pytz
        tz = pytz.timezone(config_dashboard.backend_config.TIMEZONE if hasattr(config_dashboard, 'backend_config') else "UTC")
        
        df['reminder_datetime'] = pd.to_datetime(df['reminder_datetime']).dt.tz_localize('UTC').dt.tz_convert(tz).dt.tz_localize(None)
        df['created_at'] = pd.to_datetime(df['created_at']).dt.tz_localize('UTC').dt.tz_convert(tz).dt.tz_localize(None)
    return df

def get_reminder_history(status: str = 'All', days: int = 30) -> pd.DataFrame:
    with get_db_connection() as conn:
        query = "SELECT * FROM reminders"
        params = []
        
        conditions = []
        if status != 'All':
            conditions.append("status = ?")
            params.append(status.lower())
            
        if days > 0:
            cutoff_date = datetime.utcnow() - timedelta(days=days)
            conditions.append("created_at >= ?")
            params.append(cutoff_date.isoformat())
            
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
            
        query += " ORDER BY reminder_datetime DESC"
        df = pd.read_sql_query(query, conn, params=params)
        
    if not df.empty:
        import pytz
        tz = pytz.timezone(config_dashboard.backend_config.TIMEZONE if hasattr(config_dashboard, 'backend_config') else "UTC")
        
        df['reminder_datetime'] = pd.to_datetime(df['reminder_datetime']).dt.tz_localize('UTC').dt.tz_convert(tz).dt.tz_localize(None)
        df['created_at'] = pd.to_datetime(df['created_at']).dt.tz_localize('UTC').dt.tz_convert(tz).dt.tz_localize(None)
        df['triggered_at'] = pd.to_datetime(df['triggered_at']).dt.tz_localize('UTC').dt.tz_convert(tz).dt.tz_localize(None)
    return df

def get_reminder_stats() -> Dict[str, int]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status, COUNT(*) as count FROM reminders GROUP BY status")
        rows = cursor.fetchall()
        
    stats = {'total': 0, 'pending': 0, 'completed': 0, 'failed': 0, 'cancelled': 0}
    for row in rows:
        status = row['status']
        count = row['count']
        if status in stats:
            stats[status] = count
        stats['total'] += count
    return stats

def get_success_rate() -> float:
    stats = get_reminder_stats()
    completed = stats.get('completed', 0)
    failed = stats.get('failed', 0)
    total_finished = completed + failed
    
    if total_finished == 0:
        return 0.0
    return round((completed / total_finished) * 100, 1)

def get_peak_hours() -> list:
    df = get_reminder_history(status='All', days=30)
    if df.empty:
        return []
    
    df['hour'] = df['reminder_datetime'].dt.hour
    return df['hour'].value_counts().head(5).index.tolist()

def get_messages_stats() -> int:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM messages")
        return cursor.fetchone()[0]

# Mutation functions accessed via Dashboard
def delete_reminder(reminder_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        conn.commit()

def mark_status(reminder_id: int, status: str):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE reminders SET status = ? WHERE id = ?", (status, reminder_id))
        conn.commit()

def export_reminders_csv() -> str:
    df = get_reminder_history(status='All', days=0) # All time
    return df.to_csv(index=False)

# --- TASKS QUERIES ---

def get_pending_tasks(user_phone: Optional[str] = None) -> pd.DataFrame:
    with get_db_connection() as conn:
        query = "SELECT * FROM tasks WHERE status = 'pending'"
        params = []
        if user_phone:
            query += " AND user_phone = ?"
            params.append(user_phone)
        query += " ORDER BY end_datetime ASC, created_at DESC"
        df = pd.read_sql_query(query, conn, params=params)
        
    if not df.empty:
        import pytz
        tz = pytz.timezone(config_dashboard.backend_config.TIMEZONE if hasattr(config_dashboard, 'backend_config') else "UTC")
        
        # safely handle datetime parsing when some columns might have empty dates
        df['end_datetime'] = pd.to_datetime(df['end_datetime'], errors='coerce')
        if df['end_datetime'].notna().any():
            df['end_datetime'] = df['end_datetime'].dt.tz_localize('UTC').dt.tz_convert(tz).dt.tz_localize(None)
            
        df['created_at'] = pd.to_datetime(df['created_at']).dt.tz_localize('UTC').dt.tz_convert(tz).dt.tz_localize(None)
    return df

def get_task_history(status: str = 'All', days: int = 30) -> pd.DataFrame:
    with get_db_connection() as conn:
        query = "SELECT * FROM tasks"
        params = []
        
        conditions = []
        if status != 'All':
            conditions.append("status = ?")
            params.append(status.lower())
            
        if days > 0:
            cutoff_date = datetime.utcnow() - timedelta(days=days)
            conditions.append("created_at >= ?")
            params.append(cutoff_date.isoformat())
            
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
            
        query += " ORDER BY created_at DESC"
        df = pd.read_sql_query(query, conn, params=params)
        
    if not df.empty:
        import pytz
        tz = pytz.timezone(config_dashboard.backend_config.TIMEZONE if hasattr(config_dashboard, 'backend_config') else "UTC")
        
        df['end_datetime'] = pd.to_datetime(df['end_datetime'], errors='coerce')
        if df['end_datetime'].notna().any():
            df['end_datetime'] = df['end_datetime'].dt.tz_localize('UTC').dt.tz_convert(tz).dt.tz_localize(None)
            
        df['created_at'] = pd.to_datetime(df['created_at']).dt.tz_localize('UTC').dt.tz_convert(tz).dt.tz_localize(None)
    return df

def get_task_stats() -> Dict[str, int]:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT status, COUNT(*) as count FROM tasks GROUP BY status")
        rows = cursor.fetchall()
        
    stats = {'total': 0, 'pending': 0, 'completed': 0}
    for row in rows:
        status = row['status']
        count = row['count']
        if status in stats:
            stats[status] = count
        stats['total'] += count
    return stats

def delete_task(task_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        conn.commit()

def mark_task_status(task_id: int, status: str):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("UPDATE tasks SET status = ? WHERE id = ?", (status, task_id))
        conn.commit()

def export_tasks_csv() -> str:
    df = get_task_history(status='All', days=0)
    return df.to_csv(index=False)


# ─────────────────────────────────────────────────────────────────────────────
# IDEA STORE QUERIES
# ─────────────────────────────────────────────────────────────────────────────

def get_all_ideas(search: str = "") -> pd.DataFrame:
    """Returns all ideas, optionally filtered by subject search string."""
    with get_db_connection() as conn:
        if search:
            df = pd.read_sql_query(
                "SELECT * FROM ideas WHERE subject LIKE ? ORDER BY created_at DESC",
                conn,
                params=[f"%{search}%"]
            )
        else:
            df = pd.read_sql_query(
                "SELECT * FROM ideas ORDER BY created_at DESC",
                conn
            )

    if not df.empty:
        import pytz
        tz = pytz.timezone(
            config_dashboard.backend_config.TIMEZONE
            if hasattr(config_dashboard, 'backend_config') else "UTC"
        )
        df['created_at'] = (
            pd.to_datetime(df['created_at'])
            .dt.tz_localize('UTC')
            .dt.tz_convert(tz)
            .dt.tz_localize(None)
        )
    return df


def delete_idea_by_id(idea_id: int):
    """Deletes an idea by its ID (admin action — no user_phone scope on dashboard)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM ideas WHERE id = ?", (idea_id,))
        conn.commit()


def get_idea_stats() -> dict:
    """Returns idea count statistics."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM ideas")
        total = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM ideas WHERE media_type IS NOT NULL")
        with_media = cursor.fetchone()[0]
    return {"total": total, "with_media": with_media}


# ─────────────────────────────────────────────────────────────────────────────
# NOTES STORE QUERIES
# ─────────────────────────────────────────────────────────────────────────────

def get_all_notes(search: str = "") -> pd.DataFrame:
    """Returns all notes, optionally filtered by subject search string."""
    with get_db_connection() as conn:
        if search:
            df = pd.read_sql_query(
                "SELECT * FROM notes WHERE subject LIKE ? ORDER BY created_at DESC",
                conn,
                params=[f"%{search}%"]
            )
        else:
            df = pd.read_sql_query(
                "SELECT * FROM notes ORDER BY created_at DESC",
                conn
            )

    if not df.empty:
        import pytz
        tz = pytz.timezone(
            config_dashboard.backend_config.TIMEZONE
            if hasattr(config_dashboard, 'backend_config') else "UTC"
        )
        df['created_at'] = (
            pd.to_datetime(df['created_at'])
            .dt.tz_localize('UTC')
            .dt.tz_convert(tz)
            .dt.tz_localize(None)
        )
    return df


def delete_note_by_id(note_id: int):
    """Deletes a note by its ID (admin action on dashboard)."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        conn.commit()


def get_note_stats() -> dict:
    """Returns note count statistics."""
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM notes")
        total = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM notes WHERE media_type IS NOT NULL")
        with_media = cursor.fetchone()[0]
    return {"total": total, "with_media": with_media}


# ─────────────────────────────────────────────────────────────────────────────
# RESOURCE QUERIES
# ─────────────────────────────────────────────────────────────────────────────

def get_all_resources(search: str = "") -> pd.DataFrame:
    with get_db_connection() as conn:
        if search:
            df = pd.read_sql_query("SELECT * FROM resources WHERE subject LIKE ? ORDER BY created_at DESC", conn, params=[f"%{search}%"])
        else:
            df = pd.read_sql_query("SELECT * FROM resources ORDER BY created_at DESC", conn)
    if not df.empty:
        import pytz
        tz = pytz.timezone(config_dashboard.backend_config.TIMEZONE if hasattr(config_dashboard, 'backend_config') else "UTC")
        df['created_at'] = pd.to_datetime(df['created_at']).dt.tz_localize('UTC').dt.tz_convert(tz).dt.tz_localize(None)
    return df

def delete_resource_by_id(resource_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM resources WHERE id = ?", (resource_id,))
        conn.commit()

def get_resource_stats() -> dict:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM resources")
        total = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM resources WHERE media_type IS NOT NULL")
        with_media = cursor.fetchone()[0]
    return {"total": total, "with_media": with_media}


# ─────────────────────────────────────────────────────────────────────────────
# DUMP QUERIES
# ─────────────────────────────────────────────────────────────────────────────

def get_all_dumps(search: str = "") -> pd.DataFrame:
    with get_db_connection() as conn:
        if search:
            df = pd.read_sql_query("SELECT * FROM dumps WHERE subject LIKE ? ORDER BY created_at DESC", conn, params=[f"%{search}%"])
        else:
            df = pd.read_sql_query("SELECT * FROM dumps ORDER BY created_at DESC", conn)
    if not df.empty:
        import pytz
        tz = pytz.timezone(config_dashboard.backend_config.TIMEZONE if hasattr(config_dashboard, 'backend_config') else "UTC")
        df['created_at'] = pd.to_datetime(df['created_at']).dt.tz_localize('UTC').dt.tz_convert(tz).dt.tz_localize(None)
    return df

def delete_dump_by_id(dump_id: int):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM dumps WHERE id = ?", (dump_id,))
        conn.commit()

def get_dump_stats() -> dict:
    with get_db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM dumps")
        total = cursor.fetchone()[0]
        cursor.execute("SELECT COUNT(*) FROM dumps WHERE media_type IS NOT NULL")
        with_media = cursor.fetchone()[0]
    return {"total": total, "with_media": with_media}


# ──────────────────────────────────────────────────────────────────────────────
# USER MANAGEMENT (allowed_users table)
# ──────────────────────────────────────────────────────────────────────────────

def get_all_allowed_users() -> pd.DataFrame:
    """Returns all allowed users as a DataFrame for the admin UI."""
    with get_db_connection() as conn:
        try:
            df = pd.read_sql_query(
                "SELECT id, phone, label, is_admin, added_at FROM allowed_users ORDER BY is_admin DESC, added_at ASC",
                conn
            )
            return df
        except Exception:
            return pd.DataFrame(columns=["id", "phone", "label", "is_admin", "added_at"])


def add_allowed_user(phone: str, label: str = "") -> tuple[bool, str]:
    """
    Inserts a new allowed user into the DB.
    Returns (success: bool, message: str).
    """
    phone_clean = phone.strip()
    if not phone_clean.isdigit():
        return False, "Phone number must contain digits only (e.g. 923001234567)."
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                INSERT INTO allowed_users (phone, label, is_admin, added_at)
                VALUES (?, ?, 0, ?)
                """,
                (phone_clean, label.strip(), datetime.utcnow().isoformat())
            )
            conn.commit()
            return True, f"User {phone_clean} added successfully."
        except sqlite3.IntegrityError:
            return False, f"Phone {phone_clean} is already in the allowed list."


def remove_allowed_user(phone: str) -> tuple[bool, str]:
    """
    Removes an allowed user and all their data from every table.
    Admin (is_admin=1) cannot be removed.
    Returns (success: bool, message: str).
    """
    with get_db_connection() as conn:
        cursor = conn.cursor()

        cursor.execute("SELECT is_admin FROM allowed_users WHERE phone = ?", (phone,))
        row = cursor.fetchone()
        if not row:
            return False, f"User {phone} not found."
        if row["is_admin"] == 1:
            return False, "The admin user cannot be removed."

        # Cascade delete all user data
        tables = ["reminders", "tasks", "notes", "ideas",
                  "resources", "dumps", "messages", "conversation_state"]
        for table in tables:
            cursor.execute(f"DELETE FROM {table} WHERE user_phone = ?", (phone,))

        cursor.execute("DELETE FROM allowed_users WHERE phone = ?", (phone,))
        conn.commit()
        return True, f"User {phone} and all their data have been deleted."


# ─────────────────────────────────────────────────────────────────────────────
# ATTACHMENT QUERIES (for frontend card expansion)
# ─────────────────────────────────────────────────────────────────────────────

def get_attachments(section: str, entry_id: int) -> list:
    """
    Returns all attachment rows for a given section entry.
    Each row has: id, media_type, media_path, original_name, created_at.
    Returns empty list if attachments table doesn't exist yet.
    """
    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT id, media_type, media_path, original_name, created_at
                FROM attachments
                WHERE section = ? AND entry_id = ?
                ORDER BY created_at ASC
                """,
                (section, entry_id)
            )
            return [dict(row) for row in cursor.fetchall()]
    except Exception:
        return []


# ─────────────────────────────────────────────────────────────────────────────
# DELETE FUNCTIONS (called by frontend delete buttons)
# ─────────────────────────────────────────────────────────────────────────────

def delete_idea_by_id(idea_id: int) -> None:
    with get_db_connection() as conn:
        conn.execute("DELETE FROM attachments WHERE section = 'idea' AND entry_id = ?", (idea_id,))
        conn.execute("DELETE FROM ideas WHERE id = ?", (idea_id,))
        conn.commit()


def delete_note_by_id(note_id: int) -> None:
    with get_db_connection() as conn:
        conn.execute("DELETE FROM attachments WHERE section = 'note' AND entry_id = ?", (note_id,))
        conn.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        conn.commit()


def delete_resource_by_id(resource_id: int) -> None:
    with get_db_connection() as conn:
        conn.execute("DELETE FROM attachments WHERE section = 'resource' AND entry_id = ?", (resource_id,))
        conn.execute("DELETE FROM resources WHERE id = ?", (resource_id,))
        conn.commit()


def delete_dump_by_id(dump_id: int) -> None:
    with get_db_connection() as conn:
        conn.execute("DELETE FROM attachments WHERE section = 'dump' AND entry_id = ?", (dump_id,))
        conn.execute("DELETE FROM dumps WHERE id = ?", (dump_id,))
        conn.commit()


# ─────────────────────────────────────────────────────────────────────────────
# WIPE USER CONTENT
# ─────────────────────────────────────────────────────────────────────────────

def wipe_user_content(phone: str) -> tuple[bool, str, int]:
    """
    Wipes ALL content for a user (ideas, notes, resources, dumps, reminders, tasks,
    attachments, temp_media, messages, conversation_state) but KEEPS the account.
    Also deletes associated media files from disk.

    Returns: (success, message, rows_deleted)
    """
    import os
    import sys

    # Pull BASE_DIR from config_dashboard so file resolution matches the backend
    try:
        import config_dashboard
        base_dir = config_dashboard.BASE_DIR
    except Exception:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    try:
        with get_db_connection() as conn:
            cursor = conn.cursor()

            # Collect all media paths before deleting
            media_paths = []
            for table, col in [("ideas", "media_path"), ("notes", "media_path"),
                                ("resources", "media_path"), ("dumps", "media_path")]:
                try:
                    cursor.execute(
                        f"SELECT {col} FROM {table} WHERE user_phone = ? AND {col} IS NOT NULL",
                        (phone,)
                    )
                    for row in cursor.fetchall():
                        if row[0]:
                            media_paths.append(row[0])
                except Exception:
                    pass

            # Attachment paths
            try:
                cursor.execute(
                    "SELECT media_path FROM attachments WHERE user_phone = ? AND media_path IS NOT NULL",
                    (phone,)
                )
                for row in cursor.fetchall():
                    if row[0]:
                        media_paths.append(row[0])
            except Exception:
                pass

            # Temp media paths
            try:
                cursor.execute(
                    "SELECT file_path FROM temp_media WHERE user_phone = ? AND file_path IS NOT NULL",
                    (phone,)
                )
                for row in cursor.fetchall():
                    if row[0]:
                        media_paths.append(row[0])
            except Exception:
                pass

            # Delete all content rows
            total_deleted = 0
            content_tables = [
                "ideas", "notes", "resources", "dumps",
                "reminders", "tasks", "attachments", "temp_media",
                "messages", "conversation_state"
            ]
            for table in content_tables:
                try:
                    cursor.execute(f"DELETE FROM {table} WHERE user_phone = ?", (phone,))
                    total_deleted += cursor.rowcount
                except Exception:
                    pass

            conn.commit()

        # Delete files from disk
        files_deleted = 0
        for path in media_paths:
            abs_path = path if os.path.isabs(path) else os.path.join(base_dir, path)
            try:
                if os.path.isfile(abs_path):
                    os.remove(abs_path)
                    files_deleted += 1
            except Exception:
                pass

        return True, f"✅ Wiped {total_deleted} DB rows and {files_deleted} media file(s) for {phone}.", total_deleted

    except Exception as e:
        return False, f"❌ Wipe failed: {e}", 0
