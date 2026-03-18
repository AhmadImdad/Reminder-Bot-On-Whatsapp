import re
from datetime import datetime, timedelta
import logging
from typing import Optional, Tuple, Dict, Any

from utils import local_to_utc, utc_to_local
from groq_client import extract_reminder_info
import config

logger = logging.getLogger(__name__)

def parse_date_time_string(date_str: str, time_str: str) -> Optional[datetime]:
    """Combines YYYY-MM-DD and HH:MM strings into a local datetime, then converts to UTC."""
    try:
        dt_str = f"{date_str} {time_str}"
        local_dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M")
        
        # Prevent setting reminders in the past (allow a 1-minute buffer)
        # utc_to_local returns an offset-aware datetime, so we strip tzinfo to match the naive local_dt
        now_local = utc_to_local(datetime.utcnow()).replace(tzinfo=None)
        if local_dt < now_local - timedelta(minutes=1):
            logger.warning(f"Attempted to set reminder in the past: {local_dt}")
            return None
            
        # Extract naive UTC time for database storage (which expects naive UTC)
        return local_to_utc(local_dt)
    except ValueError as e:
        logger.error(f"Error parsing date/time ({date_str}, {time_str}): {e}")
        return None

def process_natural_language_reminder(text: str) -> list:
    """
    Wrapper around LLM extraction to process natural language into structured data.
    Returns a list of action dictionaries.
    """
    now_local_str = utc_to_local(datetime.utcnow()).strftime("%A, %Y-%m-%d %H:%M:%S")
    
    # Use LLaMA via Groq to extract details
    extracted_data = extract_reminder_info(text, now_local_str)
    
    if not extracted_data or "actions" not in extracted_data:
        # Fallback if LLM fails
        return [{
            "intent": "none",
            "error": "Failed to extract details.",
            "confidence": "low"
        }]
    
    actions = extracted_data.get("actions", [])
    
    for action in actions:
        intent = action.get("intent", "none")
        if intent in ["add_reminder", "add_task"]:
            date_str = action.get("date")
            time_str = action.get("time")
            
            # Default missing times when a date is provided
            if date_str and not time_str:
                if intent == "add_task":
                    time_str = "23:59" # End of day for tasks
                else:
                    time_str = "09:00" # Morning for reminders
                    
            if date_str and time_str:
                parsed_dt = parse_date_time_string(date_str, time_str)
                if not parsed_dt:
                    # Date was in the past or invalid
                    action["confidence"] = "low"
                    action["date"] = None
                    action["time"] = None
                    action["error"] = "The specified time is in the past."
                else:
                    action["parsed_datetime_utc"] = parsed_dt.isoformat()
                    
    return actions
