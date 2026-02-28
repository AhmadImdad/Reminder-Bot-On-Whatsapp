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
