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
    6. "list_tasks", "list_pending_tasks", "list_completed_tasks", or "list_reminders" depends on what the user asks to see. If they just say "show tasks", it is "list_tasks". If they explicitly say "show pending tasks", it is "list_pending_tasks". If they explicitly say "show completed tasks", it is "list_completed_tasks".
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


def classify_idea_intent(text: str) -> Optional[Dict[str, Any]]:
    """
    Determines if a message is an explicit idea submission by checking if the
    LAST sentence contains the word 'idea'. If so, extracts subject and description.

    Returns:
        {"is_idea": True, "subject": "...", "description": "..."} if it is an idea.
        {"is_idea": False} if it is not.
        None if the LLM call fails.
    """
    prompt = f"""
    You are a strict message classifier for a WhatsApp bot's "Idea Store" feature.

    Your ONLY job is to determine if the following message is an idea that the user
    wants to save, by checking a very specific rule.

    THE RULE:
    - Read the LAST sentence (or last line) of the message.
    - If the last sentence contains the word "idea" (in any form, any capitalisation,
      e.g. "this is an idea", "store idea", "idea box", "just an idea", "idea!"), 
      the message IS an idea submission.
    - If the last sentence does NOT contain the word "idea", it is NOT an idea.
      Even if the rest of the message mentions an idea, only the LAST sentence matters.
    - Never guess. Apply this rule strictly.

    IF it IS an idea, extract:
    - subject: The very first sentence of the message (trim whitespace).
    - description: All text between the first and last sentence. If there is nothing
      between first and last sentences, return an empty string "".

    Message:
    ---
    {text}
    ---

    Respond ONLY with a valid JSON object. No extra text.
    Schema:
    {{
        "is_idea": boolean,
        "subject": string or null,
        "description": string or null
    }}
    """
    try:
        completion = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",
            temperature=0.0,
            max_tokens=200,
            response_format={"type": "json_object"}
        )
        response_content = completion.choices[0].message.content
        if not response_content:
            return None
        return json.loads(response_content)
    except Exception as e:
        logger.error(f"Failed to classify idea intent: {e}")
        return None


def classify_note_intent(text: str) -> Optional[Dict[str, Any]]:
    """
    Determines if a message is an explicit note submission by checking if the
    LAST sentence contains the word 'note'. If so, extracts subject and description.

    Returns:
        {"is_note": True, "subject": "...", "description": "..."} if it is a note.
        {"is_note": False} if it is not.
        None if the LLM call fails.
    """
    prompt = f"""
    You are a strict message classifier for a WhatsApp bot's "Notes Store" feature.

    Your ONLY job is to determine if the following message is a note that the user
    wants to save, by checking a very specific rule.

    THE RULE:
    - Read the LAST sentence (or last line) of the message.
    - If the last sentence contains the word "note" (in any form, any capitalisation,
      e.g. "this is a note", "store note", "note box", "just a note", "note!"), 
      the message IS a note submission.
    - If the last sentence does NOT contain the word "note", it is NOT a note.
      Even if the rest of the message mentions a note, only the LAST sentence matters.
    - Never guess. Apply this rule strictly.

    IF it IS a note, extract:
    - subject: The very first sentence of the message (trim whitespace).
    - description: All text between the first and last sentence. If there is nothing
      between first and last sentences, return an empty string "".

    Message:
    ---
    {text}
    ---

    Respond ONLY with a valid JSON object. No extra text.
    Schema:
    {{
        "is_note": boolean,
        "subject": string or null,
        "description": string or null
    }}
    """
    try:
        completion = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",
            temperature=0.0,
            max_tokens=200,
            response_format={"type": "json_object"}
        )
        response_content = completion.choices[0].message.content
        if not response_content:
            return None
        return json.loads(response_content)
    except Exception as e:
        logger.error(f"Failed to classify note intent: {e}")
        return None


def classify_resource_intent(text: str) -> Optional[Dict[str, Any]]:
    """Determines if a message is a resource submission by checking if the LAST sentence contains the word 'resource'."""
    prompt = f"""
    You are a strict message classifier for a WhatsApp bot's "Resources Store" feature.

    Your ONLY job is to determine if the following message is a resource that the user
    wants to save, by checking a very specific rule.

    THE RULE:
    - Read the LAST sentence (or last line) of the message.
    - If the last sentence contains the word "resource" (in any form, any capitalisation,
      e.g. "this is a resource", "store resource", "resource box", "just a resource", "resource!"), 
      the message IS a resource submission.
    - If the last sentence does NOT contain the word "resource", it is NOT a resource.
      Even if the rest of the message mentions a resource, only the LAST sentence matters.
    - Never guess. Apply this rule strictly.

    IF it IS a resource, extract:
    - subject: The very first sentence of the message (trim whitespace).
    - description: All text between the first and last sentence. If there is nothing
      between first and last sentences, return an empty string "".

    Message:
    ---
    {text}
    ---

    Respond ONLY with a valid JSON object. No extra text.
    Schema:
    {{
        "is_resource": boolean,
        "subject": string or null,
        "description": string or null
    }}
    """
    try:
        completion = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",
            temperature=0.0,
            max_tokens=200,
            response_format={"type": "json_object"}
        )
        response_content = completion.choices[0].message.content
        if not response_content: return None
        return json.loads(response_content)
    except Exception as e:
        logger.error(f"Failed to classify resource intent: {e}")
        return None


def classify_dump_intent(text: str) -> Optional[Dict[str, Any]]:
    """Determines if a message is a dump submission by checking if the LAST sentence contains the word 'dump'."""
    prompt = f"""
    You are a strict message classifier for a WhatsApp bot's "Dump Store" feature.

    Your ONLY job is to determine if the following message is a dump that the user
    wants to save, by checking a very specific rule.

    THE RULE:
    - Read the LAST sentence (or last line) of the message.
    - If the last sentence contains the word "dump" (in any form, any capitalisation,
      e.g. "this is a dump", "store dump", "dump box", "just a dump", "dump!"), 
      the message IS a dump submission.
    - If the last sentence does NOT contain the word "dump", it is NOT a dump.
      Even if the rest of the message mentions a dump, only the LAST sentence matters.
    - Never guess. Apply this rule strictly.

    IF it IS a dump, extract:
    - subject: The very first sentence of the message (trim whitespace).
    - description: All text between the first and last sentence. If there is nothing
      between first and last sentences, return an empty string "".

    Message:
    ---
    {text}
    ---

    Respond ONLY with a valid JSON object. No extra text.
    Schema:
    {{
        "is_dump": boolean,
        "subject": string or null,
        "description": string or null
    }}
    """
    try:
        completion = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",
            temperature=0.0,
            max_tokens=200,
            response_format={"type": "json_object"}
        )
        response_content = completion.choices[0].message.content
        if not response_content: return None
        return json.loads(response_content)
    except Exception as e:
        logger.error(f"Failed to classify dump intent: {e}")
        return None
