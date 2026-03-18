import logging
import json
from typing import Optional, Dict, Any
from groq import Groq

import config

logger = logging.getLogger(__name__)

# Initialize Groq client
client = Groq(api_key=config.GROQ_API_KEY)

def transcribe_audio(audio_file_path: str) -> Optional[str]:
    """Transcribes an audio file using Groq's Whisper API."""
    try:
        with open(audio_file_path, "rb") as file:
            transcription = client.audio.transcriptions.create(
                file=(audio_file_path, file.read()),
                model="whisper-large-v3",
                response_format="text",
                language="en" # Assuming English primary, but Whisper handles multi-language well
            )
            return transcription
    except Exception as e:
        logger.error(f"Failed to transcribe audio from {audio_file_path}: {e}")
        return None

def extract_reminder_info(text: str, current_time: str) -> Optional[Dict[str, Any]]:
    """
    Extracts structured reminder information from raw text using LLaMA.
    Returns a dictionary containing intent, task_description, date, time, target_list_id and confidence.
    """
    prompt = f"""
    You are an intelligent NLP router for a smart WhatsApp bot that handles both Reminders and a Task List.
    Current Date and Time: {current_time}
    
    Analyze the following user message and extract details.
    Message: "{text}"
    
    Respond ONLY with a valid JSON object matching this schema exactly, and nothing else:
    {{
        "intent": string (MUST be one of: "add_reminder", "add_task", "list_tasks", "list_reminders", "remove_task", "complete_task", "none"),
        "task_description": string (the action or event, e.g. "Buy groceries", or null if not applicable),
        "date": string (YYYY-MM-DD format, or null if not clear),
        "time": string (HH:MM format in 24-hour time, or null if not clear),
        "target_list_id": integer (if removing or completing a specific task by its number in the list, e.g. "remove task 2" -> 2. Otherwise null),
        "confidence": string ("low", "medium", or "high" based on clarity of intent)
    }}
    
    Rules:
    1. If the user asks to be reminded ("remind me to...", "set a reminder"), intent is "add_reminder".
    2. If the user asks to add something to their task list or just states a task ("add a task to...", "I need to..."), intent is "add_task".
    3. Both tasks and reminders can have dates/times. Use Current Date and Time to calculate relatives ("tomorrow", "in 2 hours").
    4. Provide task description precisely, stripping prefix words like "remind me to" or "add a task to".
    5. Ensure times are in 24-hour HH:MM format (e.g., 5 PM -> 17:00, "on 10" -> 10:00).
    6. "list_tasks" vs "list_reminders" depends on what the user asks to see.
    7. "target_list_id" is only used for remove_task or complete_task, when they specify an ID.
    8. If a specific day is mentioned but the calculation is ambiguous, use the closest future date matching the description.
    """
    
    try:
        completion = client.chat.completions.create(
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            model="llama-3.3-70b-versatile", # Using the stable versatile version
            temperature=0.0,
            max_tokens=256,
            response_format={"type": "json_object"}
        )
        
        response_content = completion.choices[0].message.content
        if not response_content:
            return None
            
        return json.loads(response_content)
    except Exception as e:
        logger.error(f"Failed to extract reminder information: {e}")
        return None
