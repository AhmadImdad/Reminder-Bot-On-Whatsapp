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
            msg = format_reminders_table(reminders)
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

def format_tasks_table(tasks: list) -> str:
    """Formats a list of tasks into an ASCII table optimized for mobile."""
    if not tasks:
        return "You have no active tasks."
    
    table = "```text\n+---+----------------+---------+-------+\n"
    table += "|ID | Task Name      | End Time|Status |\n"
    table += "+---+----------------+---------+-------+\n"
    
    for i, t in enumerate(tasks):
        list_id = i + 1
        name = t['task_name']
        if len(name) > 14:
            name = name[:11] + "..."
        name = name.ljust(14)
        
        end_time = "None"
        if t['end_datetime']:
            try:
                dt_val = t['end_datetime']
                if isinstance(dt_val, str):
                    dt_val = datetime.fromisoformat(dt_val.replace(' ', 'T'))
                dt = utc_to_local(dt_val.replace(tzinfo=None))
                end_time = dt.strftime("%I:%M %p")
                if end_time.startswith("0"): end_time = end_time[1:]
            except Exception as e:
                logger.error(f"Task Date Error: {e}")
        end_time = end_time.ljust(7)
        
        status = "Pending" if t['status'] == 'pending' else "Done"
        status = status.ljust(7)
        
        table += f"|{str(list_id).ljust(3)}| {name} | {end_time} |{status}|\n"
        table += "+---+----------------+---------+-------+\n"
    
    table += "```"
    return table

def format_reminders_table(reminders: list) -> str:
    """Formats a list of reminders into an ASCII table optimized for mobile."""
    if not reminders:
        return "You have no pending reminders."
    
    table = "```text\n+---+----------------+---------------+\n"
    table += "|ID | Reminder       | Time          |\n"
    table += "+---+----------------+---------------+\n"
    
    for r in reminders:
        list_id = r['id']
        name = r['task']
        if len(name) > 14:
            name = name[:11] + "..."
        name = name.ljust(14)
        
        dt_str = "None"
        if r['reminder_datetime']:
            try:
                dt_val = r['reminder_datetime']
                if isinstance(dt_val, str):
                    dt_val = datetime.fromisoformat(dt_val.replace(' ', 'T'))
                dt = utc_to_local(dt_val.replace(tzinfo=None))
                dt_str = dt.strftime("%d %b %I:%M%p").replace(" 0", " ")
            except Exception as e:
                logger.error(f"Reminder Date Error: {e}")
        
        dt_str = dt_str[:13].ljust(13)
        
        table += f"|{str(list_id).ljust(3)}| {name} | {dt_str} |\n"
        table += "+---+----------------+---------------+\n"
    
    table += "```"
    return table

def handle_idle_state(chat_id: str, message_data: Dict[str, Any], message_type: str):
    """Processes message when bot is idle (expecting a new command/reminder)."""
    text = extract_text_from_message(message_data, message_type)
    if not text:
        green_api_client.send_message(chat_id, "I couldn't understand that message. Please send text or a voice note.")
        return
        
    extracted = process_natural_language_reminder(text)
    intent = extracted.get("intent", "none")
    
    if intent == "none":
        green_api_client.send_message(chat_id, "I couldn't understand what you want to do. Please try again.")
        return
        
    # Router logic
    if intent == "list_tasks":
        tasks = database.get_user_tasks(chat_id)
        msg = format_tasks_table(tasks)
        green_api_client.send_message(chat_id, msg)
        return
        
    elif intent == "list_reminders":
        handle_commands(chat_id, "list reminders")
        return
        
    elif intent == "remove_task":
        list_id_to_remove = extracted.get("target_list_id")
        if list_id_to_remove is None:
            green_api_client.send_message(chat_id, "Please specify which task number you want to remove.")
            return
        success = database.delete_task_by_offset(chat_id, list_id_to_remove - 1)
        if success:
            tasks = database.get_user_tasks(chat_id)
            green_api_client.send_message(chat_id, "✅ Task removed.\n\n" + format_tasks_table(tasks))
        else:
            green_api_client.send_message(chat_id, f"❌ Could not find active task number {list_id_to_remove}.")
        return
        
    elif intent == "complete_task":
        list_id_to_complete = extracted.get("target_list_id")
        if list_id_to_complete is None:
            green_api_client.send_message(chat_id, "Please specify which task number you want to complete.")
            return
        success = database.mark_task_completed_by_offset(chat_id, list_id_to_complete - 1)
        if success:
            tasks = database.get_user_tasks(chat_id)
            green_api_client.send_message(chat_id, "✅ Task completed!\n\n" + format_tasks_table(tasks))
        else:
            green_api_client.send_message(chat_id, f"❌ Could not find active task number {list_id_to_complete}.")
        return
        
    elif intent == "add_task":
        task_desc = extracted.get("task_description", "")
        if not task_desc:
            green_api_client.send_message(chat_id, "Please tell me what the task is.")
            return
            
        dt = None
        if "parsed_datetime_utc" in extracted and extracted["parsed_datetime_utc"]:
            dt = datetime.fromisoformat(extracted["parsed_datetime_utc"])
            
        database.add_task(chat_id, task_desc, dt)
        tasks = database.get_user_tasks(chat_id)
        msg = "✅ Task added successfully!\n\n" + format_tasks_table(tasks)
        green_api_client.send_message(chat_id, msg)
        return
        
    elif intent == "add_reminder":
        task = extracted.get("task_description", "")
        confidence = extracted.get("confidence", "low")
        
        if confidence == "high" and "parsed_datetime_utc" in extracted and extracted["parsed_datetime_utc"]:
            # All good, save immediately
            dt = datetime.fromisoformat(extracted["parsed_datetime_utc"])
            database.add_reminder(chat_id, task, dt)
            
            dt_str = format_datetime_for_user(dt)
            green_api_client.send_message(chat_id, f"✅ Reminder set: {task} on {dt_str}")
            
        elif confidence == "medium" and "parsed_datetime_utc" in extracted and extracted["parsed_datetime_utc"]:
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
