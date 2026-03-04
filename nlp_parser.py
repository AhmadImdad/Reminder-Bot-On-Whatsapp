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

def process_natural_language_reminder(text: str) -> Dict[str, Any]:
    """
    Wrapper around LLM extraction to process natural language into structured data.
    """
    now_local_str = utc_to_local(datetime.utcnow()).strftime("%A, %Y-%m-%d %H:%M:%S")
    
    # Use LLaMA via Groq to extract details
    extracted_data = extract_reminder_info(text, now_local_str)
    
    if not extracted_data:
        # Fallback if LLM fails
        return {
            "is_reminder": False,
            "error": "Failed to extract reminder details.",
            "confidence": "low"
        }
    
    # Additional validation
    if extracted_data.get("is_reminder"):
        date_str = extracted_data.get("date")
        time_str = extracted_data.get("time")
        
        if date_str and time_str:
            parsed_dt = parse_date_time_string(date_str, time_str)
            if not parsed_dt:
                # Date was in the past or invalid
                extracted_data["confidence"] = "low"
                extracted_data["date"] = None
                extracted_data["time"] = None
                extracted_data["error"] = "The specified time is in the past."
            else:
                extracted_data["parsed_datetime_utc"] = parsed_dt.isoformat()
                
    return extracted_data
