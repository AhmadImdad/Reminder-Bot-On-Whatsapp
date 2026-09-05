import logging
import json
from typing import Optional, Dict, Any

from google import genai
from google.genai import types

import config

logger = logging.getLogger(__name__)

# ── Client initialisation ─────────────────────────────────────────────────────
# Uses the new `google-genai` SDK (google.genai).
# API key is loaded from GEMINI_API_KEY in .env via config.py.
client = genai.Client(api_key=config.GEMINI_API_KEY)

# ── Flash model fallback chain ────────────────────────────────────────────────
# Ordered by RPM (highest first) so we exhaust the most generous quotas last.
# If a model hits its rate limit (429) or fails for any reason, the next one
# in the list is tried automatically. All 6 combined give ~50 RPM total.
FLASH_MODELS = [
    "gemini-3.1-flash-lite",   # 15 RPM  ← try first (highest quota)
    "gemini-3.5-flash-lite",   # 15 RPM  ← try second
    "gemini-3.5-flash",        #  5 RPM
    "gemini-3.6-flash",        #  5 RPM
    "gemini-3.7-flash",        #  5 RPM
    "gemini-3.8-flash",        #  5 RPM  ← last resort
]

# Audio transcription still uses Groq Whisper (best-in-class for voice).
# Fall back to Gemini multimodal if Groq is unavailable.
_GROQ_AVAILABLE = False
try:
    from groq import Groq as _Groq
    _groq_client = _Groq(api_key=config.GROQ_API_KEY)
    _GROQ_AVAILABLE = bool(config.GROQ_API_KEY)
except Exception:
    pass


# ── Core helper: call Gemini with automatic model fallback ───────────────────

def _gemini_json(prompt: str, max_tokens: int = 512) -> Optional[str]:
    """
    Send a prompt to Gemini and return the raw JSON string from the response.

    Tries each model in FLASH_MODELS in order. If a model hits its rate limit
    (HTTP 429) or fails for any other reason, the next model is tried.
    Returns None only if every model in the chain fails.
    """
    last_error = None

    for model in FLASH_MODELS:
        try:
            response = client.models.generate_content(
                model=model,
                contents=prompt,
                config=types.GenerateContentConfig(
                    temperature=0.0,
                    max_output_tokens=max_tokens,
                    response_mime_type="application/json",
                ),
            )
            logger.debug(f"Gemini [{model}] responded successfully.")
            return response.text

        except Exception as e:
            error_str = str(e)
            # Detect quota / rate-limit errors
            if "429" in error_str or "quota" in error_str.lower() or "rate" in error_str.lower() or "exhausted" in error_str.lower():
                logger.warning(f"Gemini [{model}] quota hit — trying next model. ({e})")
            else:
                logger.warning(f"Gemini [{model}] failed — trying next model. ({e})")
            last_error = e
            continue  # move to next model in the chain

    logger.error(f"All Gemini Flash models exhausted. Last error: {last_error}")
    return None


# ── Audio transcription ───────────────────────────────────────────────────────

def transcribe_audio(audio_file_path: str) -> Optional[str]:
    """
    Transcribes an audio file.

    Primary: Groq Whisper (fast, accurate).
    Fallback: Gemini multimodal transcription.
    """
    # Try Groq Whisper first
    if _GROQ_AVAILABLE:
        try:
            with open(audio_file_path, "rb") as f:
                transcription = _groq_client.audio.transcriptions.create(
                    file=(audio_file_path, f.read()),
                    model="whisper-large-v3",
                    response_format="text",
                    language="en",
                )
            return transcription
        except Exception as e:
            logger.warning(f"Groq Whisper failed, falling back to Gemini: {e}")

    # Gemini fallback — loop through flash models until one works
    for model in FLASH_MODELS:
        try:
            with open(audio_file_path, "rb") as f:
                audio_bytes = f.read()

            response = client.models.generate_content(
                model=model,
                contents=[
                    types.Part.from_bytes(data=audio_bytes, mime_type="audio/ogg"),
                    "Transcribe this audio exactly as spoken. Return only the transcript text, nothing else.",
                ],
            )
            logger.debug(f"Audio transcribed by Gemini [{model}]")
            return response.text.strip()
        except Exception as e:
            logger.warning(f"Gemini [{model}] audio transcription failed — trying next. ({e})")
            continue

    logger.error("All Gemini Flash models failed for audio transcription.")
    return None


# ── NLP: reminder / task extraction ──────────────────────────────────────────

def extract_reminder_info(text: str, current_time: str) -> Optional[Dict[str, Any]]:
    """
    Extracts structured reminder/task information from raw text using Gemini.
    Returns a dict with an 'actions' list, or None on failure.
    """
    prompt = f"""
    You are an intelligent NLP router for a smart WhatsApp bot that handles both Reminders and a Task List.
    Current Date and Time: {current_time}
    
    Analyze the following user message and extract details.
    Message: "{text}"
    
    Respond ONLY with a valid JSON object matching this schema exactly, and nothing else:
    {{
        "actions": [
            {{
                "intent": string (MUST be one of: "add_reminder", "add_task", "list_tasks", "list_pending_tasks", "list_completed_tasks", "list_reminders", "remove_task", "complete_task", "none"),
                "task_description": string (the action or event, e.g. "Buy groceries", or null if not applicable),
                "date": string (YYYY-MM-DD format, or null if not clear),
                "time": string (HH:MM format in 24-hour time, or null if not clear),
                "target_list_id": integer (if removing or completing a specific task by its number in the list, e.g. "remove task 2" -> 2. Otherwise null),
                "confidence": string ("low", "medium", or "high" based on clarity of intent)
            }}
        ]
    }}
    
    If the user gives multiple commands in one message (e.g., "Add a task to X, then set a reminder for Y"), respond with multiple action objects in the array. If there is only one command, return an array of length 1.
    
    Rules:
    1. If the user asks to be reminded ("remind me to...", "set a reminder"), intent is "add_reminder".
    2. If the user asks to add something to their task list or just states a task ("add a task to...", "I need to..."), intent is "add_task".
    3. Both tasks and reminders can have dates/times. Use Current Date and Time to calculate relatives ("tomorrow", "in 2 hours").
    4. Provide task description precisely, stripping prefix words like "remind me to" or "add a task to".
    5. Ensure times are in 24-hour HH:MM format (e.g., 5 PM -> 17:00, "on 10" -> 10:00).
    6. "list_tasks", "list_pending_tasks", "list_completed_tasks", or "list_reminders" depends on what the user asks to see.
    7. "target_list_id" is only used for remove_task or complete_task, when they specify an ID.
    8. If a specific day is mentioned but the calculation is ambiguous, use the closest future date matching the description.
    """

    raw = _gemini_json(prompt, max_tokens=512)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Gemini JSON response: {e} | raw={raw[:200]}")
        return None


# ── NLP: idea classification ──────────────────────────────────────────────────

def classify_idea_intent(text: str) -> Optional[Dict[str, Any]]:
    """
    Determines if a message is an idea submission and extracts subject/description.
    Returns {"is_idea": True, "subject": "...", "description": "..."} or {"is_idea": False} or None.
    """
    prompt = f"""
    You are an AI assistant for a WhatsApp bot's "Idea Store" feature.

    The following message has already been identified as an idea submission.
    Your ONLY job is to extract the subject and description perfectly.

    EXTRACTION RULES:
    - subject: The very first sentence of the message (trim whitespace).
    - description: All text between the first sentence and the last sentence. If there is nothing between the first and last sentences, return an empty string "".

    Message:
    ---
    {text}
    ---

    Respond ONLY with a valid JSON object. No extra text.
    Schema:
    {{
        "is_idea": true,
        "subject": string,
        "description": string
    }}
    """
    raw = _gemini_json(prompt, max_tokens=256)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse idea intent JSON: {e}")
        return None


# ── NLP: note classification ──────────────────────────────────────────────────

def classify_note_intent(text: str) -> Optional[Dict[str, Any]]:
    """
    Determines if a message is a note submission and extracts subject/description.
    Returns {"is_note": True, "subject": "...", "description": "..."} or {"is_note": False} or None.
    """
    prompt = f"""
    You are an AI assistant for a WhatsApp bot's "Notes Store" feature.

    The following message has already been identified as a note submission.
    Your ONLY job is to extract the subject and description perfectly.

    EXTRACTION RULES:
    - subject: The very first sentence of the message (trim whitespace).
    - description: All text between the first sentence and the last sentence. If there is nothing between the first and last sentences, return an empty string "".

    Message:
    ---
    {text}
    ---

    Respond ONLY with a valid JSON object. No extra text.
    Schema:
    {{
        "is_note": true,
        "subject": string,
        "description": string
    }}
    """
    raw = _gemini_json(prompt, max_tokens=256)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse note intent JSON: {e}")
        return None


# ── NLP: resource classification ─────────────────────────────────────────────

def classify_resource_intent(text: str) -> Optional[Dict[str, Any]]:
    """Determines if a message is a resource submission and extracts subject/description."""
    prompt = f"""
    You are an AI assistant for a WhatsApp bot's "Resources Store" feature.

    The following message has already been identified as a resource submission.
    Your ONLY job is to extract the subject and description perfectly.

    EXTRACTION RULES:
    - subject: The very first sentence of the message (trim whitespace).
    - description: All text between the first sentence and the last sentence. If there is nothing between the first and last sentences, return an empty string "".

    Message:
    ---
    {text}
    ---

    Respond ONLY with a valid JSON object. No extra text.
    Schema:
    {{
        "is_resource": true,
        "subject": string,
        "description": string
    }}
    """
    raw = _gemini_json(prompt, max_tokens=256)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse resource intent JSON: {e}")
        return None


# ── NLP: dump classification ──────────────────────────────────────────────────

def classify_dump_intent(text: str) -> Optional[Dict[str, Any]]:
    """Determines if a message is a dump submission and extracts subject/description."""
    prompt = f"""
    You are an AI assistant for a WhatsApp bot's "Dump Store" feature.

    The following message has already been identified as a dump submission.
    Your ONLY job is to extract the subject and description perfectly.

    EXTRACTION RULES:
    - subject: The very first sentence of the message (trim whitespace).
    - description: All text between the first sentence and the last sentence. If there is nothing between the first and last sentences, return an empty string "".

    Message:
    ---
    {text}
    ---

    Respond ONLY with a valid JSON object. No extra text.
    Schema:
    {{
        "is_dump": true,
        "subject": string,
        "description": string
    }}
    """
    raw = _gemini_json(prompt, max_tokens=256)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse dump intent JSON: {e}")
        return None
