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
    Returns a dictionary containing is_reminder, task, date, time, and confidence.
    """
    prompt = f"""
    You are a precise date and time extraction assistant for a reminder bot.
    Current Date and Time: {current_time}
    
    Analyze the following user message and extract the reminder details.
    Message: "{text}"
    
    Respond ONLY with a valid JSON object matching this schema exactly, and nothing else:
    {{
        "is_reminder": boolean (true if the user is asking to set a reminder, false otherwise),
        "task": string (the action or event to be reminded about, short and concise),
        "date": string (YYYY-MM-DD format, or null if not clear),
        "time": string (HH:MM format in 24-hour time, or null if not clear),
        "confidence": string ("low", "medium", or "high" based on clarity of time and intent)
    }}
    
    Rules for date/time conversion:
    1. If the user says "tomorrow morning", infer the date for tomorrow and time around "09:00".
    2. If the user says "in 2 hours", calculate the relative time based on the Current Date and Time (including weekday) provided above.
    3. If the user mentions a specific day of the week (e.g., "Saturday this week" or "next Friday"), calculate the correct date relative to the Current Date and Time.
    4. Ensure times are in 24-hour HH:MM format (e.g., 5 PM -> 17:00).
    5. Provide task description precisely, stripping away prefix words like "remind me to".
    """
    
    try:
        completion = client.chat.completions.create(
            messages=[
                {
                    "role": "user",
                    "content": prompt,
                }
            ],
            model="llama-3.1-8b-instant", # Universal active fast model
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
