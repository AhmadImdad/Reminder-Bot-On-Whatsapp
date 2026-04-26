import logging
import os
import uuid
from datetime import datetime
from typing import Dict, Any

import database
import green_api_client
import groq_client
import config
from nlp_parser import (
    process_natural_language_reminder, 
    process_idea_message, 
    process_note_message,
    process_resource_message,
    process_dump_message
)
from utils import format_datetime_for_user, local_to_utc, utc_to_local

# Directory where idea media files are stored locally
IDEA_MEDIA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ideas_media")
# Directory where note media files are stored locally
NOTE_MEDIA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "notes_media")
# Directory where resource media files are stored locally
RESOURCE_MEDIA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resources_media")
# Directory where dump media files are stored locally
DUMP_MEDIA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dumps_media")

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

    # ── UNDO COMMAND ──────────────────────────────────────────────────────────
    elif text_lower == "undo":
        state = database.get_conversation_state(chat_id)
        context = state.get("context", {})
        last_actions = context.get("last_actions", [])

        if not last_actions:
            green_api_client.send_message(chat_id, "There is nothing to undo right now.")
            database.update_conversation_state(chat_id, "idle", {})
            return True

        undone = []
        for action in last_actions:
            a_type = action.get("type")
            a_id = action.get("id")
            a_media = action.get("media_path")

            if a_type == "idea":
                if database.delete_idea(a_id, chat_id):
                    if a_media and os.path.exists(a_media):
                        try: os.remove(a_media)
                        except: pass
                    undone.append("💡 Idea")
            elif a_type == "note":
                if database.delete_note(a_id, chat_id):
                    if a_media and os.path.exists(a_media):
                        try: os.remove(a_media)
                        except: pass
                    undone.append("📓 Note")
            elif a_type == "resource":
                if database.delete_resource(a_id, chat_id):
                    if a_media and os.path.exists(a_media):
                        try: os.remove(a_media)
                        except: pass
                    undone.append("🔗 Resource")
            elif a_type == "dump":
                if database.delete_dump(a_id, chat_id):
                    if a_media and os.path.exists(a_media):
                        try: os.remove(a_media)
                        except: pass
                    undone.append("🗑️ Dump")
            elif a_type == "task":
                if database.delete_task(a_id, chat_id):
                    undone.append("📝 Task")
            elif a_type == "reminder":
                if database.delete_reminder(a_id, chat_id):
                    undone.append("⏰ Reminder")

        if undone:
            green_api_client.send_message(chat_id, f"✅ Undone: {', '.join(undone)}")
        else:
            green_api_client.send_message(chat_id, "❌ Could not undo the last action (it may have already been deleted).")
            
        database.update_conversation_state(chat_id, "idle", {})
        return True

    # ── IDEA STORE COMMANDS ────────────────────────────────────────────────────
    elif text_lower in ["my ideas", "list ideas", "show ideas", "ideas"]:
        ideas = database.get_ideas(chat_id)
        if not ideas:
            green_api_client.send_message(chat_id, "💡 Your idea store is empty! Send any message ending with the word 'idea' to save one.")
        else:
            green_api_client.send_message(chat_id, format_ideas_table(ideas))
        database.update_conversation_state(chat_id, "idle", {})
        return True

    elif text_lower.startswith("show idea ") or text_lower.startswith("idea "):
        # Extract the numeric ID
        parts = text_lower.replace("show idea", "").replace("idea", "").strip().split()
        if parts and parts[0].isdigit():
            idea_id = int(parts[0])
            handle_idea_show(chat_id, idea_id)
        else:
            green_api_client.send_message(chat_id, "Please specify which idea you want to see. Example: 'show idea 3'")
        database.update_conversation_state(chat_id, "idle", {})
        return True

    elif text_lower.startswith("delete idea "):
        parts = text_lower.replace("delete idea", "").strip().split()
        if parts and parts[0].isdigit():
            idea_id = int(parts[0])
            success = database.delete_idea(idea_id, chat_id)
            if success:
                green_api_client.send_message(chat_id, f"🗑️ Idea #{idea_id} deleted.")
            else:
                green_api_client.send_message(chat_id, f"❌ Could not find idea #{idea_id}.")
        else:
            green_api_client.send_message(chat_id, "Please specify which idea to delete. Example: 'delete idea 3'")
        database.update_conversation_state(chat_id, "idle", {})
        return True
    # ── END IDEA COMMANDS ──────────────────────────────────────────────────────

    # ── NOTE STORE COMMANDS ───────────────────────────────────────────────────
    elif text_lower in ["my notes", "list notes", "show notes", "notes"]:
        notes = database.get_notes(chat_id)
        if not notes:
            green_api_client.send_message(chat_id, "📓 Your notes store is empty! Send any message ending with the word 'note' to save one.")
        else:
            green_api_client.send_message(chat_id, format_notes_table(notes))
        database.update_conversation_state(chat_id, "idle", {})
        return True

    elif text_lower.startswith("show note ") or text_lower.startswith("note "):
        parts = text_lower.replace("show note", "").replace("note", "").strip().split()
        if parts and parts[0].isdigit():
            note_id = int(parts[0])
            handle_note_show(chat_id, note_id)
        else:
            green_api_client.send_message(chat_id, "Please specify which note you want to see. Example: 'show note 3'")
        database.update_conversation_state(chat_id, "idle", {})
        return True

    elif text_lower.startswith("delete note "):
        parts = text_lower.replace("delete note", "").strip().split()
        if parts and parts[0].isdigit():
            note_id = int(parts[0])
            success = database.delete_note(note_id, chat_id)
            if success:
                green_api_client.send_message(chat_id, f"🗑️ Note #{note_id} deleted.")
            else:
                green_api_client.send_message(chat_id, f"❌ Could not find note #{note_id}.")
        else:
            green_api_client.send_message(chat_id, "Please specify which note to delete. Example: 'delete note 3'")
        database.update_conversation_state(chat_id, "idle", {})
        return True
    # ── END NOTE COMMANDS ──────────────────────────────────────────────────────

    # ── RESOURCE COMMANDS ──────────────────────────────────────────────────────
    elif text_lower in ["my resources", "list resources", "show resources", "resources"]:
        resources = database.get_resources(chat_id)
        if not resources:
            green_api_client.send_message(chat_id, "🔗 Your resources store is empty! Send any message ending with the word 'resource' to save one.")
        else:
            green_api_client.send_message(chat_id, format_resources_table(resources))
        database.update_conversation_state(chat_id, "idle", {})
        return True

    elif text_lower.startswith("show resource ") or text_lower.startswith("resource "):
        parts = text_lower.replace("show resource", "").replace("resource", "").strip().split()
        if parts and parts[0].isdigit():
            resource_id = int(parts[0])
            handle_resource_show(chat_id, resource_id)
        else:
            green_api_client.send_message(chat_id, "Please specify which resource you want to see. Example: 'show resource 3'")
        database.update_conversation_state(chat_id, "idle", {})
        return True

    elif text_lower.startswith("delete resource "):
        parts = text_lower.replace("delete resource", "").strip().split()
        if parts and parts[0].isdigit():
            resource_id = int(parts[0])
            success = database.delete_resource(resource_id, chat_id)
            if success:
                green_api_client.send_message(chat_id, f"🗑️ Resource #{resource_id} deleted.")
            else:
                green_api_client.send_message(chat_id, f"❌ Could not find resource #{resource_id}.")
        else:
            green_api_client.send_message(chat_id, "Please specify which resource to delete. Example: 'delete resource 3'")
        database.update_conversation_state(chat_id, "idle", {})
        return True

    # ── DUMP COMMANDS ──────────────────────────────────────────────────────────
    elif text_lower in ["my dumps", "list dumps", "show dumps", "dumps", "my dump"]:
        dumps = database.get_dumps(chat_id)
        if not dumps:
            green_api_client.send_message(chat_id, "🗑️ Your dump store is empty! Send any message ending with the word 'dump' to save one.")
        else:
            green_api_client.send_message(chat_id, format_dumps_table(dumps))
        database.update_conversation_state(chat_id, "idle", {})
        return True

    elif text_lower.startswith("show dump ") or text_lower.startswith("dump "):
        parts = text_lower.replace("show dump", "").replace("dump", "").strip().split()
        if parts and parts[0].isdigit():
            dump_id = int(parts[0])
            handle_dump_show(chat_id, dump_id)
        else:
            green_api_client.send_message(chat_id, "Please specify which dump you want to see. Example: 'show dump 3'")
        database.update_conversation_state(chat_id, "idle", {})
        return True

    elif text_lower.startswith("delete dump "):
        parts = text_lower.replace("delete dump", "").strip().split()
        if parts and parts[0].isdigit():
            dump_id = int(parts[0])
            success = database.delete_dump(dump_id, chat_id)
            if success:
                green_api_client.send_message(chat_id, f"🗑️ Dump #{dump_id} deleted.")
            else:
                green_api_client.send_message(chat_id, f"❌ Could not find dump #{dump_id}.")
        else:
            green_api_client.send_message(chat_id, "Please specify which dump to delete. Example: 'delete dump 3'")
        database.update_conversation_state(chat_id, "idle", {})
        return True

    # ── DIRECTORY COMMANDS ─────────────────────────────────────────────────────
    elif text_lower in ["show sections", "show me all the sections", "show all sections", "sections", "menu"]:
        sections_msg = (
            "🗂️ *Reminder-Bot Sections Directory*\n\n"
            "⏰ *Reminders:* `my reminders`\n"
            "📝 *Tasks:* `my tasks`\n"
            "💡 *Ideas:* `my ideas`\n"
            "📓 *Notes:* `my notes`\n"
            "🔗 *Resources:* `my resources`\n"
            "🗑️ *Dumps:* `my dumps`\n\n"
            "_Send a message ending with the section name (e.g., 'this is an idea') to save into it._"
        )
        green_api_client.send_message(chat_id, sections_msg)
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

def format_tasks_table(tasks: list, filter_status: str = "all") -> str:
    """Formats a list of tasks into an ASCII table optimized for mobile. Optionally filters by status."""
    if not tasks:
        return "You have no active tasks."
    
    # Check if there are any tasks matching the filter
    has_matches = False
    for t in tasks:
        if filter_status == "all" or t['status'] == filter_status:
            has_matches = True
            break
            
    if not has_matches:
        return f"You have no {filter_status} tasks."
    
    N = max([len(t['task_name']) for t in tasks] + [9])
    dash_col = "-" * (N + 2)
    header_col = " Task Name".ljust(N + 2)
    
    table = f"```text\n+---+{dash_col}+----------------+------+\n"
    table += f"|ID |{header_col}| End Time       |Status|\n"
    table += f"+---+{dash_col}+----------------+------+\n"
    
    for i, t in enumerate(tasks):
        if filter_status != "all" and t['status'] != filter_status:
            continue
            
        list_id = i + 1
        name = t['task_name'].ljust(N)
        
        end_time = "None"
        if t['end_datetime']:
            try:
                dt_val = t['end_datetime']
                if isinstance(dt_val, str):
                    dt_val = datetime.fromisoformat(dt_val.replace(' ', 'T'))
                dt = utc_to_local(dt_val.replace(tzinfo=None))
                end_time = dt.strftime("%d %b %I:%M%p").replace(" 0", " ")
            except Exception as e:
                logger.error(f"Task Date Error: {e}")
        end_time = end_time[:14].ljust(14)
        
        status = "Pend" if t['status'] == 'pending' else "Done"
        status = status.ljust(6)
        
        table += f"|{str(list_id).ljust(3)}| {name} | {end_time} |{status}|\n"
        table += f"+---+{dash_col}+----------------+------+\n"
    
    table += "```"
    return table

def format_reminders_table(reminders: list) -> str:
    """Formats a list of reminders into an ASCII table optimized for mobile."""
    if not reminders:
        return "You have no pending reminders."
    
    N = max([len(r['task']) for r in reminders] + [8])
    dash_col = "-" * (N + 2)
    header_col = " Reminder".ljust(N + 2)
    
    table = f"```text\n+---+{dash_col}+---------------+\n"
    table += f"|ID |{header_col}| Time          |\n"
    table += f"+---+{dash_col}+---------------+\n"
    
    for r in reminders:
        list_id = r['id']
        name = r['task'].ljust(N)
        
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
        table += f"+---+{dash_col}+---------------+\n"
    
    table += "```"
    return table

def handle_idle_state(chat_id: str, message_data: Dict[str, Any], message_type: str):
    """Processes message when bot is idle (expecting a new command/reminder)."""
    text = extract_text_from_message(message_data, message_type)
    if not text:
        green_api_client.send_message(chat_id, "I couldn't understand that message. Please send text or a voice note.")
        return

    # ── IDEA PRE-CHECK (runs before reminder/task pipeline) ───────────────────
    # This is phase 1 of a two-phase detection. _last_sentence_contains_idea() is
    # a cheap Python regex check. Only if it passes do we call the LLM.
    idea_result = process_idea_message(text)
    if idea_result is None:
        # LLM call failed — notify user and bail safely
        green_api_client.send_message(chat_id, "⚠️ I had trouble processing your message. Please try again.")
        return
    if idea_result.get("is_idea"):
        handle_idea_capture(chat_id, idea_result, message_data, message_type)
        return
    # ── END IDEA PRE-CHECK ────────────────────────────────────────────────────

    # ── NOTE PRE-CHECK (runs after idea check, before reminder/task pipeline) ──
    note_result = process_note_message(text)
    if note_result is None:
        green_api_client.send_message(chat_id, "⚠️ I had trouble processing your message. Please try again.")
        return
    if note_result.get("is_note"):
        handle_note_capture(chat_id, note_result, message_data, message_type)
        return
    # ── END NOTE PRE-CHECK ────────────────────────────────────────────────────

    # ── RESOURCE PRE-CHECK ────────────────────────────────────────────────────
    resource_result = process_resource_message(text)
    if resource_result is None:
        green_api_client.send_message(chat_id, "⚠️ I had trouble processing your message. Please try again.")
        return
    if resource_result.get("is_resource"):
        handle_resource_capture(chat_id, resource_result, message_data, message_type)
        return
    # ── END RESOURCE PRE-CHECK ────────────────────────────────────────────────

    # ── DUMP PRE-CHECK ────────────────────────────────────────────────────────
    dump_result = process_dump_message(text)
    if dump_result is None:
        green_api_client.send_message(chat_id, "⚠️ I had trouble processing your message. Please try again.")
        return
    if dump_result.get("is_dump"):
        handle_dump_capture(chat_id, dump_result, message_data, message_type)
        return
    # ── END DUMP PRE-CHECK ────────────────────────────────────────────────────

    extracted_actions = process_natural_language_reminder(text)
    
    responses = []
    last_actions = []
    show_tasks_table = False
    task_filter_status = "all"
    show_reminders_table = False
    
    for extracted in extracted_actions:
        intent = extracted.get("intent", "none")
        
        if intent == "none":
            responses.append("I couldn't understand part of your request. Please try again.")
            continue
            
        elif intent == "list_tasks":
            show_tasks_table = True
            task_filter_status = "all"
            
        elif intent == "list_pending_tasks":
            show_tasks_table = True
            task_filter_status = "pending"

        elif intent == "list_completed_tasks":
            show_tasks_table = True
            task_filter_status = "completed"
            
        elif intent == "list_reminders":
            show_reminders_table = True
            
        elif intent == "remove_task":
            list_id_to_remove = extracted.get("target_list_id")
            if list_id_to_remove is None:
                responses.append("Please specify which task number you want to remove.")
            else:
                success = database.delete_task_by_offset(chat_id, list_id_to_remove - 1)
                if success:
                    responses.append(f"✅ Task removed.")
                    show_tasks_table = True
                else:
                    responses.append(f"❌ Could not find active task number {list_id_to_remove}.")
                    
        elif intent == "complete_task":
            list_id_to_complete = extracted.get("target_list_id")
            if list_id_to_complete is None:
                responses.append("Please specify which task number you want to complete.")
            else:
                success = database.mark_task_completed_by_offset(chat_id, list_id_to_complete - 1)
                if success:
                    responses.append(f"✅ Task completed!")
                    show_tasks_table = True
                else:
                    responses.append(f"❌ Could not find active task number {list_id_to_complete}.")
                    
        elif intent == "add_task":
            task_desc = extracted.get("task_description", "")
            if not task_desc:
                responses.append("Please tell me what the task is.")
                continue
                
            dt = None
            if "parsed_datetime_utc" in extracted and extracted["parsed_datetime_utc"]:
                dt = datetime.fromisoformat(extracted["parsed_datetime_utc"])
                
            task_id = database.add_task(chat_id, task_desc, dt)
            last_actions.append({"type": "task", "id": task_id})
            responses.append(f"✅ Task added successfully!")
            show_tasks_table = True
            
        elif intent == "add_reminder":
            task = extracted.get("task_description", "")
            confidence = extracted.get("confidence", "low")
            
            if confidence == "high" and "parsed_datetime_utc" in extracted and extracted["parsed_datetime_utc"]:
                dt = datetime.fromisoformat(extracted["parsed_datetime_utc"])
                reminder_id = database.add_reminder(chat_id, task, dt)
                last_actions.append({"type": "reminder", "id": reminder_id})
                
                dt_str = format_datetime_for_user(dt)
                responses.append(f"✅ Reminder set: {task} on {dt_str}")
                
            elif confidence == "medium" and "parsed_datetime_utc" in extracted and extracted["parsed_datetime_utc"]:
                dt = datetime.fromisoformat(extracted["parsed_datetime_utc"])
                dt_str = format_datetime_for_user(dt)
                
                responses.append(f"I understood: {task} on {dt_str}. Is this correct? Reply YES or NO.")
                context = {
                    "task": task,
                    "parsed_datetime_utc": extracted["parsed_datetime_utc"]
                }
                database.update_conversation_state(chat_id, "awaiting_confirmation", context)
                break
                
            else:
                error_msg = extracted.get("error", "")
                if error_msg == "The specified time is in the past.":
                    responses.append(f"You asked to be reminded about: {task}. But the time seems to be in the past. When should I remind you?")
                else:
                    responses.append(f"I want to remind you about: {task}. When should I remind you? Please provide date and time.")
                    
                context = {"task": task}
                database.update_conversation_state(chat_id, "awaiting_datetime", context)
                break

    final_msg = "\n".join(responses)
    if show_tasks_table:
        tasks = database.get_user_tasks(chat_id)
        final_msg += ("\n\n" if final_msg else "") + format_tasks_table(tasks, filter_status=task_filter_status)
        
    if show_reminders_table:
        reminders = database.get_user_pending_reminders(chat_id)
        final_msg += ("\n\n" if final_msg else "") + format_reminders_table(reminders)
        
    if final_msg:
        green_api_client.send_message(chat_id, final_msg)

    # Save last_actions if any tasks or reminders were created
    if last_actions:
        database.update_conversation_state(chat_id, "idle", {"last_actions": last_actions})
    elif not any(r for r in responses if "Is this correct?" in r or "When should I remind you?" in r):
        # Don't overwrite state if we are awaiting confirmation
        pass
def handle_confirmation_state(chat_id: str, message_data: Dict[str, Any], message_type: str, context: Dict[str, Any]):
    """Processes yes/no response when awaiting confirmation."""
    text = extract_text_from_message(message_data, message_type).lower().strip()
    
    # Simple regex parsing could be used, but let's check basic intents
    if text in ["yes", "y", "correct", "yeah", "yep"]:
        dt = datetime.fromisoformat(context["parsed_datetime_utc"])
        task = context["task"]
        
        reminder_id = database.add_reminder(chat_id, task, dt)
        dt_str = format_datetime_for_user(dt)
        green_api_client.send_message(chat_id, f"✅ Reminder set: {task} on {dt_str}")
        database.update_conversation_state(chat_id, "idle", {"last_actions": [{"type": "reminder", "id": reminder_id}]})
        
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
    
    extracted_actions = process_natural_language_reminder(combined_prompt)
    if not extracted_actions:
        green_api_client.send_message(chat_id, "I still couldn't understand the correct time. Please try saying it clearly, like 'Tomorrow at 6 PM'")
        return
        
    extracted = extracted_actions[0]
    
    if "parsed_datetime_utc" in extracted and extracted["parsed_datetime_utc"]:
        dt = datetime.fromisoformat(extracted["parsed_datetime_utc"])
        final_task = extracted.get("task_description", task)
        
        reminder_id = database.add_reminder(chat_id, final_task, dt)
        dt_str = format_datetime_for_user(dt)
        green_api_client.send_message(chat_id, f"✅ Reminder set: {final_task} on {dt_str}")
        database.update_conversation_state(chat_id, "idle", {"last_actions": [{"type": "reminder", "id": reminder_id}]})
    else:
        green_api_client.send_message(chat_id, "I still couldn't understand the correct time. Please try saying it clearly, like 'Tomorrow at 6 PM'")


# ─────────────────────────────────────────────────────────────────────────────
# IDEA STORE HANDLERS
# ─────────────────────────────────────────────────────────────────────────────

def _save_idea_media(message_data: Dict[str, Any], message_type: str):
    """
    Downloads the media file from a WhatsApp message and saves it to ideas_media/.
    Returns (media_type, media_path, original_name) or (None, None, None).
    Supports: imageMessage, audioMessage, pttMessage (voice note), videoMessage.
    """
    supported = {
        "imageMessage": "image",
        "audioMessage": "audio",
        "pttMessage":   "audio",
        "videoMessage": "video",
    }
    if message_type not in supported:
        return None, None, None

    media_type = supported[message_type]
    file_data = message_data.get("fileMessageData", {})
    download_url = file_data.get("downloadUrl", "")
    original_name = file_data.get("fileName", f"{media_type}_{uuid.uuid4()}")

    if not download_url:
        return None, None, None

    # Determine extension safely
    ext_map = {"image": "jpg", "audio": "ogg", "video": "mp4"}
    # Try to get extension from original filename first
    orig_ext = os.path.splitext(original_name)[1].lstrip(".")
    ext = orig_ext if orig_ext else ext_map.get(media_type, "bin")

    # Generate a safe, collision-proof filename
    safe_name = f"{uuid.uuid4().hex}.{ext}"
    os.makedirs(IDEA_MEDIA_DIR, exist_ok=True)
    save_path = os.path.join(IDEA_MEDIA_DIR, safe_name)

    if green_api_client.download_file(download_url, save_path):
        logger.info(f"Idea media saved to {save_path}")
        return media_type, save_path, original_name
    else:
        logger.error(f"Failed to download idea media from {download_url}")
        return None, None, None


def handle_idea_capture(chat_id: str, idea_result: dict,
                        message_data: Dict[str, Any], message_type: str):
    """
    Saves a confirmed idea to the database.
    Handles media attachment if present in the same message.
    """
    subject = (idea_result.get("subject") or "").strip()
    description = (idea_result.get("description") or "").strip() or None

    if not subject:
        green_api_client.send_message(
            chat_id,
            "⚠️ I detected this is an idea, but couldn't extract a subject. "
            "Please make sure your first sentence is the idea title."
        )
        return

    # Attempt to save any media attached to the message
    media_type, media_path, media_original_name = _save_idea_media(message_data, message_type)

    idea_id = database.add_idea(
        user_phone=chat_id,
        subject=subject,
        description=description,
        media_type=media_type,
        media_path=media_path,
        media_original_name=media_original_name,
    )

    media_note = f" (+ {media_type} attached)" if media_type else ""
    green_api_client.send_message(
        chat_id,
        f"💡 *Idea #{idea_id} saved!*{media_note}\n"
        f"📌 *Subject:* {subject}"
        + (f"\n📝 *Description:* {description}" if description else "")
    )
    database.update_conversation_state(chat_id, "idle", {"last_actions": [{"type": "idea", "id": idea_id, "media_path": media_path}]})


def handle_idea_show(chat_id: str, idea_id: int):
    """Retrieves and sends a single idea back to the user, including any media."""
    idea = database.get_idea_by_id(idea_id, chat_id)

    if not idea:
        green_api_client.send_message(chat_id, f"❌ Idea #{idea_id} not found.")
        return

    subject = idea["subject"]
    description = idea["description"]
    media_type = idea["media_type"]
    media_path = idea["media_path"]
    media_original_name = idea["media_original_name"] or "attachment"

    # Format the text reply
    reply = f"💡 *Idea #{idea_id}*\n📌 *Subject:* {subject}"
    if description:
        reply += f"\n📝 *Description:* {description}"
    green_api_client.send_message(chat_id, reply)

    # Send media as a follow-up message if it exists
    if media_type and media_path and os.path.exists(media_path):
        try:
            green_api_client.send_file(chat_id, media_path, media_original_name)
        except Exception as e:
            logger.error(f"Failed to send idea media for idea #{idea_id}: {e}")
            green_api_client.send_message(chat_id, "⚠️ There was an error sending the attached media.")
    elif media_type and media_path:
        # File was recorded in DB but no longer on disk
        green_api_client.send_message(chat_id, f"⚠️ The attached {media_type} file could not be found on the server.")


def format_ideas_table(ideas: list) -> str:
    """Formats a list of ideas into an ASCII table matching the bot's existing style."""
    if not ideas:
        return "Your idea store is empty."

    N = max([len(i['subject']) for i in ideas] + [7])
    dash_col = "-" * (N + 2)
    header_col = " Subject".ljust(N + 2)

    table = f"```text\n+----+{dash_col}+------+------------+\n"
    table += f"| ID |{header_col}|Media | Date       |\n"
    table += f"+----+{dash_col}+------+------------+\n"

    for idea in ideas:
        id_str = str(idea['id']).ljust(4)
        subject = idea['subject'][:N].ljust(N)
        media = (idea['media_type'] or '  -  ')[:6].ljust(6)

        created = idea['created_at']
        try:
            if isinstance(created, str):
                created = datetime.fromisoformat(created.replace(' ', 'T'))
            dt = utc_to_local(created.replace(tzinfo=None))
            date_str = dt.strftime("%d %b %Y")[:12].ljust(12)
        except Exception:
            date_str = "Unknown     "

        table += f"|{id_str}| {subject} |{media}| {date_str} |\n"
        table += f"+----+{dash_col}+------+------------+\n"

    table += "```"
    table += "\nType *show idea N* to view full details of an idea."
    return table


# ─────────────────────────────────────────────────────────────────────────────
# NOTES STORE HANDLERS
# ─────────────────────────────────────────────────────────────────────────────

def _save_note_media(message_data: Dict[str, Any], message_type: str):
    """
    Downloads the media file from a WhatsApp message and saves it to notes_media/.
    Returns (media_type, media_path, original_name) or (None, None, None).
    """
    supported = {
        "imageMessage": "image",
        "audioMessage": "audio",
        "pttMessage":   "audio",
        "videoMessage": "video",
    }
    if message_type not in supported:
        return None, None, None

    media_type = supported[message_type]
    file_data = message_data.get("fileMessageData", {})
    download_url = file_data.get("downloadUrl", "")
    original_name = file_data.get("fileName", f"{media_type}_{uuid.uuid4()}")

    if not download_url:
        return None, None, None

    ext_map = {"image": "jpg", "audio": "ogg", "video": "mp4"}
    orig_ext = os.path.splitext(original_name)[1].lstrip(".")
    ext = orig_ext if orig_ext else ext_map.get(media_type, "bin")

    safe_name = f"{uuid.uuid4().hex}.{ext}"
    os.makedirs(NOTE_MEDIA_DIR, exist_ok=True)
    save_path = os.path.join(NOTE_MEDIA_DIR, safe_name)

    if green_api_client.download_file(download_url, save_path):
        logger.info(f"Note media saved to {save_path}")
        return media_type, save_path, original_name
    else:
        logger.error(f"Failed to download note media from {download_url}")
        return None, None, None


def handle_note_capture(chat_id: str, note_result: dict,
                        message_data: Dict[str, Any], message_type: str):
    """
    Saves a confirmed note to the database.
    Handles media attachment if present in the same message.
    """
    subject = (note_result.get("subject") or "").strip()
    description = (note_result.get("description") or "").strip() or None

    if not subject:
        green_api_client.send_message(
            chat_id,
            "⚠️ I detected this is a note, but couldn't extract a subject. "
            "Please make sure your first sentence is the note title."
        )
        return

    media_type, media_path, media_original_name = _save_note_media(message_data, message_type)

    note_id = database.add_note(
        user_phone=chat_id,
        subject=subject,
        description=description,
        media_type=media_type,
        media_path=media_path,
        media_original_name=media_original_name,
    )

    media_note = f" (+ {media_type} attached)" if media_type else ""
    green_api_client.send_message(
        chat_id,
        f"📓 *Note #{note_id} saved!*{media_note}\n"
        f"📌 *Subject:* {subject}"
        + (f"\n📝 *Description:* {description}" if description else "")
    )
    database.update_conversation_state(chat_id, "idle", {"last_actions": [{"type": "note", "id": note_id, "media_path": media_path}]})


def handle_note_show(chat_id: str, note_id: int):
    """Retrieves and sends a single note back to the user, including any media."""
    note = database.get_note_by_id(note_id, chat_id)

    if not note:
        green_api_client.send_message(chat_id, f"❌ Note #{note_id} not found.")
        return

    subject = note["subject"]
    description = note["description"]
    media_type = note["media_type"]
    media_path = note["media_path"]
    media_original_name = note["media_original_name"] or "attachment"

    reply = f"📓 *Note #{note_id}*\n📌 *Subject:* {subject}"
    if description:
        reply += f"\n📝 *Description:* {description}"
    green_api_client.send_message(chat_id, reply)

    if media_type and media_path and os.path.exists(media_path):
        try:
            green_api_client.send_file(chat_id, media_path, media_original_name)
        except Exception as e:
            logger.error(f"Failed to send note media for note #{note_id}: {e}")
            green_api_client.send_message(chat_id, "⚠️ There was an error sending the attached media.")
    elif media_type and media_path:
        green_api_client.send_message(chat_id, f"⚠️ The attached {media_type} file could not be found on the server.")


def format_notes_table(notes: list) -> str:
    """Formats a list of notes into an ASCII table matching the bot's existing style."""
    if not notes:
        return "Your notes store is empty."

    N = max([len(n['subject']) for n in notes] + [7])
    dash_col = "-" * (N + 2)
    header_col = " Subject".ljust(N + 2)

    table = f"```text\n+----+{dash_col}+------+------------+\n"
    table += f"| ID |{header_col}|Media | Date       |\n"
    table += f"+----+{dash_col}+------+------------+\n"

    for note in notes:
        id_str = str(note['id']).ljust(4)
        subject = note['subject'][:N].ljust(N)
        media = (note['media_type'] or '  -  ')[:6].ljust(6)

        created = note['created_at']
        try:
            if isinstance(created, str):
                created = datetime.fromisoformat(created.replace(' ', 'T'))
            dt = utc_to_local(created.replace(tzinfo=None))
            date_str = dt.strftime("%d %b %Y")[:12].ljust(12)
        except Exception:
            date_str = "Unknown     "

        table += f"|{id_str}| {subject} |{media}| {date_str} |\n"
        table += f"+----+{dash_col}+------+------------+\n"

    table += "```"
    table += "\nType *show note N* to view full details of a note."
    return table

# ─────────────────────────────────────────────────────────────────────────────
# RESOURCE STORE HANDLERS
# ─────────────────────────────────────────────────────────────────────────────

def _save_resource_media(message_data: Dict[str, Any], message_type: str):
    supported = {"imageMessage": "image", "audioMessage": "audio", "pttMessage": "audio", "videoMessage": "video"}
    if message_type not in supported: return None, None, None
    media_type = supported[message_type]
    file_data = message_data.get("fileMessageData", {})
    download_url = file_data.get("downloadUrl", "")
    original_name = file_data.get("fileName", f"{media_type}_{uuid.uuid4()}")
    if not download_url: return None, None, None
    ext_map = {"image": "jpg", "audio": "ogg", "video": "mp4"}
    orig_ext = os.path.splitext(original_name)[1].lstrip(".")
    ext = orig_ext if orig_ext else ext_map.get(media_type, "bin")
    safe_name = f"{uuid.uuid4().hex}.{ext}"
    os.makedirs(RESOURCE_MEDIA_DIR, exist_ok=True)
    save_path = os.path.join(RESOURCE_MEDIA_DIR, safe_name)
    if green_api_client.download_file(download_url, save_path):
        return media_type, save_path, original_name
    return None, None, None

def handle_resource_capture(chat_id: str, result: dict, message_data: Dict[str, Any], message_type: str):
    subject, description = result.get("subject"), result.get("description")
    if not subject:
        green_api_client.send_message(chat_id, "⚠️ I couldn't extract a subject for this resource.")
        return
    media_type, media_path, media_original_name = _save_resource_media(message_data, message_type)
    r_id = database.add_resource(chat_id, subject, description, media_type, media_path, media_original_name)
    media_note = f" (+ {media_type} attached)" if media_type else ""
    green_api_client.send_message(
        chat_id,
        f"🔗 *Resource #{r_id} saved!*{media_note}\n📌 *Subject:* {subject}"
        + (f"\n📝 *Description:* {description}" if description else "")
    )
    database.update_conversation_state(chat_id, "idle", {"last_actions": [{"type": "resource", "id": r_id, "media_path": media_path}]})

def handle_resource_show(chat_id: str, r_id: int):
    item = database.get_resource_by_id(r_id, chat_id)
    if not item:
        green_api_client.send_message(chat_id, f"❌ Resource #{r_id} not found.")
        return
    reply = f"🔗 *Resource #{r_id}*\n📌 *Subject:* {item['subject']}"
    if item['description']: reply += f"\n📝 *Description:* {item['description']}"
    green_api_client.send_message(chat_id, reply)
    if item['media_type'] and item['media_path'] and os.path.exists(item['media_path']):
        try: green_api_client.send_file(chat_id, item['media_path'], item['media_original_name'] or "attachment")
        except: pass

def format_resources_table(items: list) -> str:
    if not items: return "Your resource store is empty."
    N = max([len(i['subject']) for i in items] + [9])
    dash_col = "-" * (N + 2)
    header_col = " Subject".ljust(N + 2)
    table = f"```text\n+---+{dash_col}+-----+\n|ID |{header_col}|Media|\n+---+{dash_col}+-----+\n"
    for i in items:
        id_str = str(i['id']).ljust(3)
        subj = i['subject'][:N].ljust(N)
        media = " Yes " if i['media_type'] else " No  "
        table += f"|{id_str}| {subj} |{media}|\n+---+{dash_col}+-----+\n"
    table += "```\nSend `show resource <ID>` to view full details."
    return table

# ─────────────────────────────────────────────────────────────────────────────
# DUMP STORE HANDLERS
# ─────────────────────────────────────────────────────────────────────────────

def _save_dump_media(message_data: Dict[str, Any], message_type: str):
    supported = {"imageMessage": "image", "audioMessage": "audio", "pttMessage": "audio", "videoMessage": "video"}
    if message_type not in supported: return None, None, None
    media_type = supported[message_type]
    file_data = message_data.get("fileMessageData", {})
    download_url = file_data.get("downloadUrl", "")
    original_name = file_data.get("fileName", f"{media_type}_{uuid.uuid4()}")
    if not download_url: return None, None, None
    ext_map = {"image": "jpg", "audio": "ogg", "video": "mp4"}
    orig_ext = os.path.splitext(original_name)[1].lstrip(".")
    ext = orig_ext if orig_ext else ext_map.get(media_type, "bin")
    safe_name = f"{uuid.uuid4().hex}.{ext}"
    os.makedirs(DUMP_MEDIA_DIR, exist_ok=True)
    save_path = os.path.join(DUMP_MEDIA_DIR, safe_name)
    if green_api_client.download_file(download_url, save_path):
        return media_type, save_path, original_name
    return None, None, None

def handle_dump_capture(chat_id: str, result: dict, message_data: Dict[str, Any], message_type: str):
    subject, description = result.get("subject"), result.get("description")
    if not subject:
        green_api_client.send_message(chat_id, "⚠️ I couldn't extract a subject for this dump.")
        return
    media_type, media_path, media_original_name = _save_dump_media(message_data, message_type)
    d_id = database.add_dump(chat_id, subject, description, media_type, media_path, media_original_name)
    media_note = f" (+ {media_type} attached)" if media_type else ""
    green_api_client.send_message(
        chat_id,
        f"🗑️ *Dump #{d_id} saved!*{media_note}\n📌 *Subject:* {subject}"
        + (f"\n📝 *Description:* {description}" if description else "")
    )
    database.update_conversation_state(chat_id, "idle", {"last_actions": [{"type": "dump", "id": d_id, "media_path": media_path}]})

def handle_dump_show(chat_id: str, d_id: int):
    item = database.get_dump_by_id(d_id, chat_id)
    if not item:
        green_api_client.send_message(chat_id, f"❌ Dump #{d_id} not found.")
        return
    reply = f"🗑️ *Dump #{d_id}*\n📌 *Subject:* {item['subject']}"
    if item['description']: reply += f"\n📝 *Description:* {item['description']}"
    green_api_client.send_message(chat_id, reply)
    if item['media_type'] and item['media_path'] and os.path.exists(item['media_path']):
        try: green_api_client.send_file(chat_id, item['media_path'], item['media_original_name'] or "attachment")
        except: pass

def format_dumps_table(items: list) -> str:
    if not items: return "Your dump store is empty."
    N = max([len(i['subject']) for i in items] + [9])
    dash_col = "-" * (N + 2)
    header_col = " Subject".ljust(N + 2)
    table = f"```text\n+---+{dash_col}+-----+\n|ID |{header_col}|Media|\n+---+{dash_col}+-----+\n"
    for i in items:
        id_str = str(i['id']).ljust(3)
        subj = i['subject'][:N].ljust(N)
        media = " Yes " if i['media_type'] else " No  "
        table += f"|{id_str}| {subj} |{media}|\n+---+{dash_col}+-----+\n"
    table += "```\nSend `show dump <ID>` to view full details."
    return table
