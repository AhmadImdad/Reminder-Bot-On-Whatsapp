import re
from datetime import datetime, timedelta
import logging
from typing import Optional, Tuple, Dict, Any

from utils import local_to_utc, utc_to_local
from groq_client import extract_reminder_info, classify_idea_intent, classify_note_intent
import groq_client
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


def _last_sentence_contains_idea(text: str) -> bool:
    """
    Fast Python pre-check (zero API cost).
    Checks whether the LAST sentence of the message ends with the word 'idea'
    (or 'ideas', 'idea box', 'idea!', etc.), meaning the user's closing
    intent is explicitly to save an idea.

    We intentionally require 'idea' near the END of the last sentence to
    prevent false positives like:
      "I had an idea earlier but remind me about dentist on Friday."
    In that message, 'idea' appears mid-sentence, not as the closing intent.
    """
    import re
    # Split on sentence-ending punctuation or newlines
    sentences = [s.strip() for s in re.split(r'[.!?\n]+', text) if s.strip()]
    if not sentences:
        return False
    last = sentences[-1].lower().rstrip('.,!? ')
    # Match if the last sentence ends with 'idea', 'ideas', 'idea box',
    # 'an idea', 'store idea', 'this is an idea', etc.
    return bool(re.search(r'\bideas?(\s+\w+){0,2}$', last))


def process_idea_message(text: str) -> Optional[dict]:
    """
    Two-phase idea detection:
      Phase 1 — Fast Python check: does the last sentence contain 'idea'?
                 If no → return None immediately (no API call made).
      Phase 2 — LLM call to extract structured subject + description.

    Returns:
        {"is_idea": True, "subject": str, "description": str}  on success
        {"is_idea": False}                                      if not an idea
        None                                                    on LLM failure
    """
    # Phase 1: cheap pre-check
    if not _last_sentence_contains_idea(text):
        return {"is_idea": False}

    # Phase 2: call LLM for structured extraction
    result = classify_idea_intent(text)
    if result is None:
        logger.error("classify_idea_intent returned None — LLM failure.")
        return None
    return result


def _last_sentence_contains_note(text: str) -> bool:
    """
    Fast Python pre-check (zero API cost) for notes.
    Checks whether the LAST sentence of the message ends with the word 'note'
    or 'notes', meaning the user's closing intent is explicitly to save a note.
    Requires 'note' near the END to avoid false positives like:
      "I took a note earlier but remind me about the meeting on Monday."
      "Set a reminder to check my notes later."
    """
    import re
    sentences = [s.strip() for s in re.split(r'[.!?\n]+', text) if s.strip()]
    if not sentences:
        return False
    last = sentences[-1].lower().rstrip('.,!? ')
    # Match if the last sentence ends with 'note', 'notes', or common suffixes
    # like 'note box', 'note store', but prevents matching 'notes later'.
    return bool(re.search(r'\bnotes?(?:\s+(?:box|app|store|book))?$', last))


def process_note_message(text: str) -> Optional[dict]:
    """
    Two-phase note detection:
      Phase 1 — Fast Python check: does the last sentence end with 'note'?
                 If no → return {"is_note": False} immediately (no API call).
      Phase 2 — LLM call to extract structured subject + description.

    Returns:
        {"is_note": True, "subject": str, "description": str}  on success
        {"is_note": False}                                      if not a note
        None                                                    on LLM failure
    """
    # Phase 1: cheap pre-check
    if not _last_sentence_contains_note(text):
        return {"is_note": False}

    # Phase 2: call LLM for structured extraction
    return groq_client.classify_note_intent(text)

def _last_sentence_contains_resource(text: str) -> bool:
    """
    Fast Python pre-check (zero API cost) for resources.
    Checks whether the LAST sentence of the message ends with the word 'resource'
    or 'resources'.
    """
    import re
    sentences = [s.strip() for s in re.split(r'[.!?\n]+', text) if s.strip()]
    if not sentences:
        return False
    last = sentences[-1].lower().rstrip('.,!? ')
    return bool(re.search(r'\bresources?(?:\s+(?:box|app|store|book|link))?$', last))


def process_resource_message(text: str) -> Optional[dict]:
    """
    Checks if the message is intended for the Resource Store using a fast local regex first.
    If it matches, it calls the LLM for precise extraction.
    Returns a dict with 'subject' and 'description' if it's a resource, else None.
    """
    if _last_sentence_contains_resource(text):
        return groq_client.classify_resource_intent(text)
    return None

def _last_sentence_contains_dump(text: str) -> bool:
    """
    Fast Python pre-check (zero API cost) for dumps.
    Checks whether the LAST sentence of the message ends with the word 'dump'
    or 'dumps'.
    """
    import re
    sentences = [s.strip() for s in re.split(r'[.!?\n]+', text) if s.strip()]
    if not sentences:
        return False
    last = sentences[-1].lower().rstrip('.,!? ')
    return bool(re.search(r'\bdumps?(?:\s+(?:box|app|store|book|here))?$', last))


def process_dump_message(text: str) -> Optional[dict]:
    """
    Checks if the message is intended for the Dump Store using a fast local regex first.
    If it matches, it calls the LLM for precise extraction.
    Returns a dict with 'subject' and 'description' if it's a dump, else None.
    """
    if _last_sentence_contains_dump(text):
        return groq_client.classify_dump_intent(text)
    return None
