import logging
import os
from datetime import datetime
from typing import Dict, Any

import database
import green_api_client
import groq_client
import config
from nlp_parser import process_natural_language_reminder
from utils import format_datetime_for_user, local_to_utc, utc_to_local

logger = logging.getLogger(__name__)

def handle_incoming_webhook(data: Dict[str, Any]):
    """Main entry point for Green API webhooks."""
    try:
        # Check if it's an incoming message
        if data.get("typeWebhook") != "incomingMessageReceived":
            return

        message_data = data.get("messageData", {})
        sender_data = data.get("senderData", {})
        
        chat_id = sender_data.get("sender", "")
        if not chat_id or "@c.us" not in chat_id:
            # Ignore group messages or malformed sender IDs
            return
            
        # Access control restriction
        if config.ALLOWED_PHONE_NUMBER and not chat_id.startswith(config.ALLOWED_PHONE_NUMBER):
            logger.warning(f"Ignored message from unauthorized number: {chat_id}")
            return
            
        message_type = message_data.get("typeMessage")
        
        # Handle simple commands first (list, cancel, help)
        if message_type == "textMessage":
            text = message_data.get("textMessageData", {}).get("textMessage", "").strip()
            if handle_commands(chat_id, text):
                return
                
        # Handle state machine for conversational flow
        state_data = database.get_conversation_state(chat_id)
        current_state = state_data["state"]
        
        if current_state == "idle":
            handle_idle_state(chat_id, message_data, message_type)
        elif current_state == "awaiting_confirmation":
            handle_confirmation_state(chat_id, message_data, message_type, state_data["context"])
        elif current_state == "awaiting_datetime":
            handle_awaiting_datetime_state(chat_id, message_data, message_type, state_data["context"])
            
    except Exception as e:
        logger.error(f"Error processing webhook: {e}", exc_info=True)
        # Attempt to notify the user
        try:
            chat_id = data.get("senderData", {}).get("sender", "")
            if chat_id:
                green_api_client.send_message(chat_id, "Sorry, I encountered an internal error while processing your request.")
        except:
            pass

def handle_commands(chat_id: str, text: str) -> bool:
    """Handles basic text commands. Returns True if a command was executed."""
    text_lower = text.lower()
    
    if text_lower in ["help", "/help"]:
        help_text = (
            "🤖 **Reminder Bot Help**\n\n"
            "I can help you set reminders! Just send me a text or voice message like:\n"
            "- 'Remind me to call mom tomorrow at 5 PM'\n"
            "- 'Set reminder for dentist next Friday 10 AM'\n\n"
            "Commands:\n"
            "- `list reminders` or `show reminders`: See your pending reminders\n"
            "- `cancel [ID]`: Cancel a specific reminder (get ID from list)\n"
            "- `help`: Show this message"
        )
        green_api_client.send_message(chat_id, help_text)
        database.update_conversation_state(chat_id, "idle", {})
        return True
        
    elif text_lower in ["list", "list reminders", "show reminders"]:
        reminders = database.get_user_pending_reminders(chat_id)
        if not reminders:
            green_api_client.send_message(chat_id, "You have no pending reminders.")
        else:
            msg = "📋 **Your Pending Reminders:**\n"
            for r in reminders:
                dt_str = format_datetime_for_user(r["reminder_datetime"])
                msg += f"\n[{r['id']}] {r['task']} - 📅 {dt_str}"
            green_api_client.send_message(chat_id, msg)
        database.update_conversation_state(chat_id, "idle", {})
        return True
        
    elif text_lower.startswith("cancel ") or text_lower.startswith("/cancel "):
        parts = text_lower.split()
        if len(parts) > 1 and parts[1].isdigit():
            r_id = int(parts[1])
            success = database.cancel_reminder(r_id, chat_id)
            if success:
                green_api_client.send_message(chat_id, f"✅ Reminder [{r_id}] has been cancelled.")
            else:
                green_api_client.send_message(chat_id, f"❌ Could not find pending reminder [{r_id}].")
        else:
            green_api_client.send_message(chat_id, "Please specify the reminder ID to cancel. Example: 'cancel 5'")
        database.update_conversation_state(chat_id, "idle", {})
        return True
        
    return False

def extract_text_from_message(message_data: Dict[str, Any], message_type: str) -> str:
    """Extracts text from textMessage or transcribes audioMessage."""
    if message_type == "textMessage":
        return message_data.get("textMessageData", {}).get("textMessage", "")
        
    elif message_type == "audioMessage": # Or pttMessage
        file_url = message_data.get("fileMessageData", {}).get("downloadUrl", "")
        if not file_url:
            return ""
            
        # Download temp file
        import tempfile
        import uuid
        
        temp_dir = tempfile.gettempdir()
        file_path = os.path.join(temp_dir, f"audio_{uuid.uuid4()}.ogg")
        
        try:
            if green_api_client.download_file(file_url, file_path):
                # Transcribe
                transcription = groq_client.transcribe_audio(file_path)
                return transcription or ""
        finally:
            # Cleanup
            if os.path.exists(file_path):
                os.remove(file_path)
                
    return ""

def handle_idle_state(chat_id: str, message_data: Dict[str, Any], message_type: str):
    """Processes message when bot is idle (expecting a new command/reminder)."""
    text = extract_text_from_message(message_data, message_type)
    if not text:
        green_api_client.send_message(chat_id, "I couldn't understand that message. Please send text or a voice note.")
        return
        
    extracted = process_natural_language_reminder(text)
    
    if not extracted.get("is_reminder"):
        green_api_client.send_message(chat_id, "I'm a reminder bot. Please ask me to set a reminder, or type 'help'.")
        return
        
    task = extracted.get("task", "")
    confidence = extracted.get("confidence", "low")
    
    if confidence == "high" and "parsed_datetime_utc" in extracted:
        # All good, save immediately
        dt = datetime.fromisoformat(extracted["parsed_datetime_utc"])
        database.add_reminder(chat_id, task, dt)
        
        dt_str = format_datetime_for_user(dt)
        green_api_client.send_message(chat_id, f"✅ Reminder set: {task} on {dt_str}")
        
    elif confidence == "medium" and "parsed_datetime_utc" in extracted:
        # Ask for confirmation
        dt = datetime.fromisoformat(extracted["parsed_datetime_utc"])
        dt_str = format_datetime_for_user(dt)
        
        msg = f"I understood: {task} on {dt_str}. Is this correct? Reply YES or NO."
        green_api_client.send_message(chat_id, msg)
        
        # Update state
        context = {
            "task": task,
            "parsed_datetime_utc": extracted["parsed_datetime_utc"]
        }
        database.update_conversation_state(chat_id, "awaiting_confirmation", context)
        
    else:
        # Missing or ambiguous date/time
        error_msg = extracted.get("error", "")
        if error_msg == "The specified time is in the past.":
            msg = f"You asked to be reminded about: {task}. But the time seems to be in the past. When should I remind you?"
        else:
            msg = f"I want to remind you about: {task}. When should I remind you? Please provide date and time."
            
        green_api_client.send_message(chat_id, msg)
        
        context = {"task": task}
        database.update_conversation_state(chat_id, "awaiting_datetime", context)

def handle_confirmation_state(chat_id: str, message_data: Dict[str, Any], message_type: str, context: Dict[str, Any]):
    """Processes yes/no response when awaiting confirmation."""
    text = extract_text_from_message(message_data, message_type).lower().strip()
    
    # Simple regex parsing could be used, but let's check basic intents
    if text in ["yes", "y", "correct", "yeah", "yep"]:
        dt = datetime.fromisoformat(context["parsed_datetime_utc"])
        task = context["task"]
        
        database.add_reminder(chat_id, task, dt)
        dt_str = format_datetime_for_user(dt)
        green_api_client.send_message(chat_id, f"✅ Reminder set: {task} on {dt_str}")
        database.update_conversation_state(chat_id, "idle", {})
        
    elif text in ["no", "n", "incorrect", "nope", "cancel"]:
        task = context["task"]
        green_api_client.send_message(chat_id, f"Got it. Please specify the correct date and time for: {task}")
        database.update_conversation_state(chat_id, "awaiting_datetime", {"task": task})
    else:
        green_api_client.send_message(chat_id, "Please reply YES or NO to confirm the reminder.")

def handle_awaiting_datetime_state(chat_id: str, message_data: Dict[str, Any], message_type: str, context: Dict[str, Any]):
    """Processes new date/time input for an existing task context."""
    text = extract_text_from_message(message_data, message_type)
    
    # We feed it to the NLP parser again, appending the context to help the LLM
    task = context["task"]
    combined_prompt = f"Set reminder for: {task}. When: {text}"
    
    extracted = process_natural_language_reminder(combined_prompt)
    
    if "parsed_datetime_utc" in extracted:
        dt = datetime.fromisoformat(extracted["parsed_datetime_utc"])
        # We use the previous task description if the LLM lost it, but LLaMA usually retains it
        final_task = extracted.get("task", task)
        
        database.add_reminder(chat_id, final_task, dt)
        dt_str = format_datetime_for_user(dt)
        green_api_client.send_message(chat_id, f"✅ Reminder set: {final_task} on {dt_str}")
        database.update_conversation_state(chat_id, "idle", {})
    else:
        green_api_client.send_message(chat_id, "I still couldn't understand the correct time. Please try saying it clearly, like 'Tomorrow at 6 PM'")
