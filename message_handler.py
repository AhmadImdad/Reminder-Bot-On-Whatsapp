import logging
import os
import re
import uuid
from datetime import datetime
from typing import Dict, Any

import database
import meta_api_client as green_api_client  # drop-in replacement for Green API
import groq_client
import config
import compressor
from nlp_parser import (
    process_natural_language_reminder,
    process_idea_message,
    process_note_message,
    process_resource_message,
    process_dump_message,
)
from utils import format_datetime_for_user, local_to_utc, utc_to_local

# Absolute path to the project root — used to compute relative media paths.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Media directories (absolute, for saving files to disk)
IDEA_MEDIA_DIR     = os.path.join(BASE_DIR, "ideas_media")
NOTE_MEDIA_DIR     = os.path.join(BASE_DIR, "notes_media")
RESOURCE_MEDIA_DIR = os.path.join(BASE_DIR, "resources_media")
DUMP_MEDIA_DIR     = os.path.join(BASE_DIR, "dumps_media")
TEMP_MEDIA_DIR     = os.path.join(BASE_DIR, "temp_media")

# Mapping from section name → (media directory, DB save function)
SECTION_META = {
    "idea":     IDEA_MEDIA_DIR,
    "note":     NOTE_MEDIA_DIR,
    "resource": RESOURCE_MEDIA_DIR,
    "dump":     DUMP_MEDIA_DIR,
}


def _resolve_media_path(media_path: str) -> str:
    """
    Resolve a media path from the database to an absolute path on the current machine.

    New entries store RELATIVE paths (e.g. 'ideas_media/abc.jpg').
    Legacy entries may still have absolute paths — they are returned unchanged
    so old data keeps working until you run the migration script.
    """
    if not media_path:
        return media_path
    if os.path.isabs(media_path):
        # Legacy absolute path — return as-is for backwards compatibility
        return media_path
    return os.path.join(BASE_DIR, media_path)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# UNIVERSAL MEDIA EXTRACTION & DOWNLOAD HELPER
# ─────────────────────────────────────────────────────────────────────────────

# ── Meta Cloud API message-type mapping ──────────────────────────────────────
# Maps the Meta 'type' field from the messages array to our internal type names.
_SUPPORTED_MEDIA_TYPES = {
    "image":    "image",
    "audio":    "audio",
    "video":    "video",
    "document": "document",
    "sticker":  "image",
}


def _extract_and_save_media(
    message_data: Dict[str, Any],
    message_type: str,
    save_dir: str,
    section_name: str = "media",
) -> tuple:
    """
    Universal media extractor for all Second Brain sections (Ideas, Notes, Resources, Dumps).

    Meta Cloud API provides a ``media_id`` (not a direct URL).  This function:
    1. Checks if message_type is a supported media type.
    2. Pulls the media_id from the Meta message payload.
    3. Calls meta_api_client.download_file(media_id, path) which resolves
       the media_id to a signed URL and downloads the file.
    4. Returns (media_type, saved_path, original_name) or (None, None, None).
    """
    if message_type not in _SUPPORTED_MEDIA_TYPES:
        logger.debug(f"[{section_name}] message_type '{message_type}' is not a media type — skipping media save.")
        return None, None, None

    media_type = _SUPPORTED_MEDIA_TYPES[message_type]

    # Meta puts the media object directly under the message type key.
    # e.g. for type="image": message_data["image"] = {"id": "...", "mime_type": "...", ...}
    media_obj = message_data.get(message_type, {})
    media_id = media_obj.get("id", "")
    original_name = media_obj.get("filename", "")  # only present for documents

    if not media_id:
        logger.error(
            f"[{section_name}] No media_id found for message_type={message_type}. "
            f"Available keys: {list(message_data.keys())}"
        )
        return None, None, None

    # Determine file extension
    mime_type = media_obj.get("mime_type", "")
    ext_map = {"image": "jpg", "audio": "ogg", "video": "mp4", "document": "pdf"}
    # Try to get extension from mime_type (e.g. 'image/jpeg' → 'jpeg')
    mime_ext = mime_type.split("/")[-1].replace("jpeg", "jpg") if mime_type else ""
    ext = mime_ext if mime_ext else ext_map.get(media_type, "bin")

    # Generate a safe, collision-proof filename
    if not original_name:
        original_name = f"{media_type}_{uuid.uuid4()}.{ext}"
    safe_name = f"{uuid.uuid4().hex}.{ext}"
    os.makedirs(save_dir, exist_ok=True)
    save_path = os.path.join(save_dir, safe_name)

    logger.info(f"[{section_name}] Downloading {media_type} via media_id={media_id} -> {save_path}")

    if green_api_client.download_file(media_id, save_path):
        logger.info(f"[{section_name}] Media saved successfully to {save_path}")
        # Store a RELATIVE path in the DB so it works on any machine/server
        relative_path = os.path.relpath(save_path, BASE_DIR)
        return media_type, relative_path, original_name
    else:
        logger.error(f"[{section_name}] download_file() FAILED for media_id={media_id}")
        return None, None, None

def _parse_meta_webhook(data: Dict[str, Any]):
    """
    Extract the first message from a Meta Cloud API webhook payload.

    Meta's webhook JSON structure:
    {
      "object": "whatsapp_business_account",
      "entry": [{
        "changes": [{
          "value": {
            "messages": [{ "from": "923...", "type": "text", "text": {"body": "..."} }],
            "contacts": [...]
          }
        }]
      }]
    }

    Returns:
        (chat_id, message_type, message_data) or (None, None, None) if not a
        valid incoming user message.
    """
    if data.get("object") != "whatsapp_business_account":
        return None, None, None

    try:
        value = data["entry"][0]["changes"][0]["value"]
    except (KeyError, IndexError):
        return None, None, None

    messages = value.get("messages")
    if not messages:
        # This is a status update (delivered/read), not an incoming message
        return None, None, None

    msg = messages[0]
    message_type = msg.get("type", "")

    # Build a unified message_data dict that contains both the top-level msg
    # fields AND the type-specific sub-object (so _extract_and_save_media
    # and extract_text_from_message can find everything in one place).
    message_data = dict(msg)  # shallow copy; includes 'type', 'from', 'id', etc.

    # phone number — Meta gives plain E.164 without any suffix
    chat_id = msg.get("from", "")

    return chat_id, message_type, message_data


def handle_incoming_webhook(data: Dict[str, Any]):
    """Main entry point for Meta WhatsApp Cloud API webhooks."""
    try:
        chat_id, message_type, message_data = _parse_meta_webhook(data)

        if not chat_id or not message_type:
            return  # status update or unsupported payload — ignore silently

        # Ignore group messages (group chat_ids from Meta contain '-')
        if "-" in chat_id:
            logger.debug(f"Ignoring group message from {chat_id}")
            return

        # Access control restriction — check against DB-managed allowed_users list
        if not database.is_phone_allowed(chat_id):
            logger.warning(f"Ignored message from unauthorized number: {chat_id}")
            return

        # Handle simple commands first (list, cancel, help)
        if message_type == "text":
            text = message_data.get("text", {}).get("body", "").strip()
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
        # ── Guided-save states ─────────────────────────────────────────────────────
        elif current_state == "awaiting_save_destination":
            handle_awaiting_save_destination_state(chat_id, message_data, message_type, state_data["context"])
        elif current_state == "awaiting_subject":
            handle_awaiting_subject_state(chat_id, message_data, message_type, state_data["context"])
        elif current_state == "awaiting_attach_target":
            handle_awaiting_attach_target_state(chat_id, message_data, message_type, state_data["context"])
        # ── Multi-action selection state ──────────────────────────────────────────
        elif current_state == "awaiting_action_selection":
            handle_awaiting_action_selection_state(chat_id, message_data, message_type, state_data["context"])
        # ── Legacy states ────────────────────────────────────────────────────────
        elif current_state == "awaiting_section_confirmation":
            handle_awaiting_section_confirmation_state(chat_id, message_data, message_type, state_data["context"])
        elif current_state == "awaiting_titan_response":
            # Handled by handle_commands at the top of the pipeline
            if message_type == "text":
                text = message_data.get("text", {}).get("body", "").strip()
                handle_commands(chat_id, text)

    except Exception as e:
        logger.error(f"Error processing webhook: {e}", exc_info=True)
        # Attempt to notify the user
        try:
            chat_id = data.get("entry", [{}])[0] \
                          .get("changes", [{}])[0] \
                          .get("value", {}) \
                          .get("messages", [{}])[0] \
                          .get("from", "")
            if chat_id:
                green_api_client.send_message(chat_id, "Sorry, I encountered an internal error while processing your request.")
        except:
            pass

def handle_commands(chat_id: str, text: str) -> bool:
    """Handles basic text commands. Returns True if a command was executed."""
    text_lower = text.lower().strip()

    # ── TITAN / OTP RESPONSE (Forgot-password flow) ───────────────────────────
    # Check if this user is in awaiting_titan_response state (admin only)
    state_data = database.get_conversation_state(chat_id)
    if state_data.get("state") == "awaiting_titan_response" and database.is_admin_phone(chat_id):
        if text_lower == "titan":
            otp = database.create_otp()
            green_api_client.send_message(
                chat_id,
                f"🔐 *Password Reset OTP:* `{otp}`\n\n"
                f"This code is valid for *10 minutes* only.\n"
                f"Enter it on the dashboard to reset your password."
            )
            database.update_conversation_state(chat_id, "idle", {})
        else:
            green_api_client.send_message(
                chat_id,
                "❌ Incorrect answer. Password reset cancelled."
            )
            database.update_conversation_state(chat_id, "idle", {})
        return True

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

    # ── TASK COMMANDS (show tasks / pending / completed) ─────────────────────
    elif text_lower in ["tasks", "my tasks", "show tasks", "list tasks"]:
        tasks = database.get_user_tasks(chat_id)
        green_api_client.send_message(chat_id, format_tasks_table(tasks, filter_status="all"))
        database.update_conversation_state(chat_id, "idle", {})
        return True

    elif text_lower in ["pending tasks", "show pending tasks", "my pending tasks", "list pending tasks"]:
        tasks = database.get_user_tasks(chat_id)
        msg = format_tasks_table(tasks, filter_status="pending")
        if tasks and not any(t['status'] == 'pending' for t in tasks):
            msg = "✅ No pending tasks — you're all caught up!"
        green_api_client.send_message(chat_id, msg)
        database.update_conversation_state(chat_id, "idle", {})
        return True

    elif text_lower in ["completed tasks", "show completed tasks", "done tasks", "finished tasks", "list completed tasks"]:
        tasks = database.get_user_tasks(chat_id)
        msg = format_tasks_table(tasks, filter_status="completed")
        if tasks and not any(t['status'] == 'completed' for t in tasks):
            msg = "🗓️ No completed tasks yet."
        green_api_client.send_message(chat_id, msg)
        database.update_conversation_state(chat_id, "idle", {})
        return True

    # ── REMINDERS (extra aliases) ────────────────────────────────────────────
    elif text_lower in ["my reminders", "reminders", "list reminders", "show reminders", "list"]:
        reminders = database.get_user_pending_reminders(chat_id)
        if not reminders:
            green_api_client.send_message(chat_id, "You have no pending reminders.")
        else:
            green_api_client.send_message(chat_id, format_reminders_table(reminders))
        database.update_conversation_state(chat_id, "idle", {})
        return True

    # ── DIRECTORY COMMANDS ─────────────────────────────────────────────────────
    elif text_lower in ["show sections", "show me all the sections", "show all sections", "sections", "menu"]:
        sections_msg = (
            "🗂️ *Reminder-Bot Sections Directory*\n\n"
            "⏰ *Reminders:* `my reminders`\n"
            "📝 *Tasks:* `my tasks`  |  `pending tasks`  |  `completed tasks`\n"
            "💡 *Ideas:* `my ideas`\n"
            "📓 *Notes:* `my notes`\n"
            "🔗 *Resources:* `my resources`\n"
            "🗑️ *Dumps:* `my dumps`\n\n"
            "_Send any message or media to save it into a section of your choice._"
        )
        green_api_client.send_message(chat_id, sections_msg)
        database.update_conversation_state(chat_id, "idle", {})
        return True

    return False

def extract_text_from_message(message_data: Dict[str, Any], message_type: str) -> str:
    """
    Extracts text from a Meta Cloud API message payload.

    Meta field layout by type:
      text     → message_data["text"]["body"]
      image    → message_data["image"]["caption"]  (optional)
      video    → message_data["video"]["caption"]  (optional)
      document → message_data["document"]["caption"] (optional)
      audio    → message_data["audio"]["id"]  (media_id — download & transcribe)
    """
    if message_type == "text":
        return message_data.get("text", {}).get("body", "")

    elif message_type in ["image", "video", "document", "sticker"]:
        # Return the caption if the user typed one alongside the media
        return message_data.get(message_type, {}).get("caption", "")

    elif message_type == "audio":
        # Meta audio messages — download via media_id then transcribe
        media_id = message_data.get("audio", {}).get("id", "")
        if not media_id:
            return ""

        import tempfile

        temp_dir = tempfile.gettempdir()
        file_path = os.path.join(temp_dir, f"audio_{uuid.uuid4()}.ogg")

        try:
            if green_api_client.download_file(media_id, file_path):
                transcription = groq_client.transcribe_audio(file_path)
                return transcription or ""
        finally:
            if os.path.exists(file_path):
                os.remove(file_path)

    return ""

def format_tasks_table(tasks: list, filter_status: str = "all") -> str:
    """Formats a list of tasks into an ASCII table. Optionally filters by status."""
    if not tasks:
        if filter_status == "pending":
            return "✅ No pending tasks — you're all caught up!"
        elif filter_status == "completed":
            return "🗒️ No completed tasks yet."
        return "You have no tasks yet."

    filtered = [t for t in tasks if filter_status == "all" or t['status'] == filter_status]
    if not filtered:
        if filter_status == "pending":
            return "✅ No pending tasks — you're all caught up!"
        elif filter_status == "completed":
            return "🗒️ No completed tasks yet."
        return f"You have no {filter_status} tasks."

    N = max([len(t['task_name']) for t in filtered] + [9])
    dash_col = "-" * (N + 2)
    header_col = " Task Name".ljust(N + 2)

    table = f"```text\n+---+{dash_col}+----------------+------+\n"
    table += f"|ID |{header_col}| End Time       |Status|\n"
    table += f"+---+{dash_col}+----------------+------+\n"

    for i, t in enumerate(filtered):
        list_id  = tasks.index(t) + 1  # display index is position in original full list
        name     = t['task_name'].ljust(N)

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

    # Pending summary line
    pending_ids = [str(tasks.index(t) + 1) for t in tasks if t['status'] == 'pending']
    if pending_ids and filter_status == "all":
        ids_str = ", ".join(f"#{pid}" for pid in pending_ids)
        table += f"\n⏳ *{len(pending_ids)} pending task(s):* {ids_str}"

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
    """Processes message when bot is idle.
    Media  → batch accumulation (15-second window, guided menu when ready).
    Text   → commands first → then reminder/task NLP.
             Single action: execute immediately with confirmation.
             Multiple actions: list them and ask user to choose which ones.
             Unrecognised: guided save offer.
    """
    # ── MEDIA ────────────────────────────────────────────────────────────
    if message_type in _SUPPORTED_MEDIA_TYPES:
        _handle_incoming_media(chat_id, message_data, message_type)
        return

    # ── TEXT / AUDIO ────────────────────────────────────────────────────────────
    text = extract_text_from_message(message_data, message_type)
    if not text:
        green_api_client.send_message(
            chat_id, "I couldn't understand that message. Please send text or a voice note."
        )
        return

    # ── REMINDER / TASK NLP PIPELINE ────────────────────────────────────────
    extracted_actions = process_natural_language_reminder(text)

    # Filter only valid (non-none) actions with real intents
    _list_intents   = {"list_tasks", "list_pending_tasks", "list_completed_tasks", "list_reminders"}
    _modify_intents = {"remove_task", "complete_task", "add_task", "add_reminder"}
    valid_actions   = [a for a in extracted_actions if a.get("intent", "none") != "none"]

    if not valid_actions:
        # Nothing the NLP understood — offer the guided save flow
        _offer_text_save(chat_id, text)
        return

    # Separate display actions (list_*) from mutating actions
    display_actions = [a for a in valid_actions if a.get("intent") in _list_intents]
    mutate_actions  = [a for a in valid_actions if a.get("intent") in _modify_intents]

    # Handle display actions immediately (they're read-only, always fine)
    for action in display_actions:
        _execute_pipeline_action(chat_id, action)

    if not mutate_actions:
        # Only display actions — done
        return

    # ── Single mutating action → execute directly ────────────────────────────
    if len(mutate_actions) == 1:
        _execute_pipeline_action(chat_id, mutate_actions[0])
        return

    # ── Multiple mutating actions → ask user to choose ────────────────────────
    numbered = _format_action_list(mutate_actions)
    database.update_conversation_state(chat_id, "awaiting_action_selection", {
        "pending_actions": [dict(a) for a in mutate_actions]
    })
    green_api_client.send_message(
        chat_id,
        f"📨 I received your message and it contains *{len(mutate_actions)} requests*:\n\n"
        f"{numbered}\n\n"
        f"Reply with the *number(s)* of the ones you want to execute — "
        f"e.g. *1*, *2*, *1 2*, or *all*.\n"
        f"Reply *cancel* to ignore all of them."
    )


def _format_action_list(actions: list) -> str:
    """Returns a numbered list of actions for display to user."""
    _intent_labels = {
        "add_reminder": "⏰ Set reminder",
        "add_task":     "📝 Add task",
        "remove_task":  "❌ Remove task",
        "complete_task":"✅ Complete task",
    }
    lines = []
    for i, action in enumerate(actions, 1):
        intent = action.get("intent", "unknown")
        label  = _intent_labels.get(intent, intent)
        desc   = action.get("task_description", action.get("task", ""))
        dt_str = ""
        if action.get("parsed_datetime_utc"):
            try:
                dt = datetime.fromisoformat(action["parsed_datetime_utc"])
                dt_str = f" — {format_datetime_for_user(dt)}"
            except Exception:
                pass
        lines.append(f"*{i}.* {label}: \"{desc}\"{dt_str}")
    return "\n".join(lines)


def _execute_pipeline_action(chat_id: str, action: dict) -> list:
    """
    Executes a single NLP-extracted action (add_reminder, add_task, list_*, etc.).
    Returns a list of last_action dicts for undo tracking.
    Sends confirmation message directly to the user.
    """
    intent = action.get("intent", "none")
    last_actions = []

    if intent == "list_tasks":
        tasks = database.get_user_tasks(chat_id)
        green_api_client.send_message(chat_id, format_tasks_table(tasks, filter_status="all"))

    elif intent == "list_pending_tasks":
        tasks = database.get_user_tasks(chat_id)
        green_api_client.send_message(chat_id, format_tasks_table(tasks, filter_status="pending"))

    elif intent == "list_completed_tasks":
        tasks = database.get_user_tasks(chat_id)
        green_api_client.send_message(chat_id, format_tasks_table(tasks, filter_status="completed"))

    elif intent == "list_reminders":
        reminders = database.get_user_pending_reminders(chat_id)
        green_api_client.send_message(chat_id, format_reminders_table(reminders))

    elif intent == "remove_task":
        list_id = action.get("target_list_id")
        if list_id is None:
            green_api_client.send_message(chat_id, "Please specify which task number you want to remove.")
        else:
            success = database.delete_task_by_offset(chat_id, list_id - 1)
            if success:
                tasks = database.get_user_tasks(chat_id)
                green_api_client.send_message(
                    chat_id,
                    f"✅ *Task #{list_id} removed.*\n\n" + format_tasks_table(tasks, filter_status="all")
                )
            else:
                green_api_client.send_message(chat_id, f"❌ Could not find active task number {list_id}.")

    elif intent == "complete_task":
        list_id = action.get("target_list_id")
        if list_id is None:
            green_api_client.send_message(chat_id, "Please specify which task number you want to complete.")
        else:
            success = database.mark_task_completed_by_offset(chat_id, list_id - 1)
            if success:
                tasks = database.get_user_tasks(chat_id)
                green_api_client.send_message(
                    chat_id,
                    f"✅ *Task #{list_id} marked as completed!*\n\n" + format_tasks_table(tasks, filter_status="all")
                )
            else:
                green_api_client.send_message(chat_id, f"❌ Could not find active task number {list_id}.")

    elif intent == "add_task":
        task_desc = action.get("task_description", "")
        if not task_desc:
            green_api_client.send_message(chat_id, "Please tell me what the task is.")
        else:
            dt = None
            if action.get("parsed_datetime_utc"):
                try:
                    dt = datetime.fromisoformat(action["parsed_datetime_utc"])
                except Exception:
                    pass
            task_id = database.add_task(chat_id, task_desc, dt)
            last_actions.append({"type": "task", "id": task_id})
            dt_line = f"\n📅 *Deadline:* {format_datetime_for_user(dt)}" if dt else ""
            tasks = database.get_user_tasks(chat_id)
            green_api_client.send_message(
                chat_id,
                f"✅ *Task saved!*\n"
                f"📝 *{task_desc}*{dt_line}\n\n"
                + format_tasks_table(tasks, filter_status="all")
            )

    elif intent == "add_reminder":
        task       = action.get("task_description", "")
        confidence = action.get("confidence", "low")

        if confidence == "high" and action.get("parsed_datetime_utc"):
            dt = datetime.fromisoformat(action["parsed_datetime_utc"])
            reminder_id = database.add_reminder(chat_id, task, dt)
            last_actions.append({"type": "reminder", "id": reminder_id})
            green_api_client.send_message(
                chat_id,
                f"✅ *Reminder set!*\n"
                f"⏰ *{task}*\n"
                f"📅 *{format_datetime_for_user(dt)}*"
            )
        elif confidence == "medium" and action.get("parsed_datetime_utc"):
            dt = datetime.fromisoformat(action["parsed_datetime_utc"])
            database.update_conversation_state(chat_id, "awaiting_confirmation", {
                "task": task, "parsed_datetime_utc": action["parsed_datetime_utc"]
            })
            green_api_client.send_message(
                chat_id,
                f"💡 I understood:\n"
                f"⏰ *{task}*\n"
                f"📅 *{format_datetime_for_user(dt)}*\n\n"
                f"Is this correct? Reply *YES* or *NO*."
            )
        else:
            error_msg = action.get("error", "")
            if error_msg == "The specified time is in the past.":
                prompt = f"You asked to be reminded about: *{task}*. But the time seems to be in the past. When should I remind you?"
            else:
                prompt = f"I want to remind you about: *{task}*. When should I remind you? Please provide date and time."
            database.update_conversation_state(chat_id, "awaiting_datetime", {"task": task})
            green_api_client.send_message(chat_id, prompt)

    if last_actions:
        database.update_conversation_state(chat_id, "idle", {"last_actions": last_actions})

    return last_actions


def handle_awaiting_action_selection_state(
        chat_id: str, message_data: Dict[str, Any],
        message_type: str, context: Dict[str, Any]):
    """
    User is choosing which of several pending actions to execute.
    They send a number (\"1\"), multiple numbers (\"1 2\"), or \"all\".
    """
    text    = extract_text_from_message(message_data, message_type).strip().lower()
    pending = context.get("pending_actions", [])

    if text in ("cancel", "none", "no", "discard"):
        database.update_conversation_state(chat_id, "idle", {})
        green_api_client.send_message(chat_id, "❌ All requests cancelled.")
        return

    if text == "all":
        chosen_indices = list(range(1, len(pending) + 1))
    else:
        # Accept: "1", "1 2", "1, 2", "1,2"
        tokens = re.split(r"[\s,]+", text)
        chosen_indices = []
        for tok in tokens:
            if tok.isdigit():
                idx = int(tok)
                if 1 <= idx <= len(pending):
                    chosen_indices.append(idx)

    if not chosen_indices:
        green_api_client.send_message(
            chat_id,
            f"Please reply with valid number(s) between 1 and {len(pending)}, or *all* / *cancel*.\n\n"
            f"{_format_action_list(pending)}"
        )
        return

    all_last_actions = []
    for idx in chosen_indices:
        last = _execute_pipeline_action(chat_id, pending[idx - 1])
        all_last_actions.extend(last)

    database.update_conversation_state(
        chat_id, "idle",
        {"last_actions": all_last_actions} if all_last_actions else {}
    )




def handle_confirmation_state(chat_id: str, message_data: Dict[str, Any], message_type: str, context: Dict[str, Any]):
    """Processes yes/no response when awaiting reminder confirmation."""
    text = extract_text_from_message(message_data, message_type).lower().strip()

    if text in ["yes", "y", "correct", "yeah", "yep"]:
        dt   = datetime.fromisoformat(context["parsed_datetime_utc"])
        task = context["task"]
        reminder_id = database.add_reminder(chat_id, task, dt)
        green_api_client.send_message(
            chat_id,
            f"✅ *Reminder set!*\n"
            f"⏰ *{task}*\n"
            f"📅 *{format_datetime_for_user(dt)}*"
        )
        database.update_conversation_state(chat_id, "idle", {"last_actions": [{"type": "reminder", "id": reminder_id}]})

    elif text in ["no", "n", "incorrect", "nope", "cancel"]:
        task = context["task"]
        green_api_client.send_message(chat_id, f"Got it. Please specify the correct date and time for: *{task}*")
        database.update_conversation_state(chat_id, "awaiting_datetime", {"task": task})
    else:
        green_api_client.send_message(chat_id, "Please reply *YES* or *NO* to confirm the reminder.")

def handle_awaiting_datetime_state(chat_id: str, message_data: Dict[str, Any], message_type: str, context: Dict[str, Any]):
    """Processes new date/time input for an existing task context."""
    text = extract_text_from_message(message_data, message_type)
    task = context["task"]
    combined_prompt = f"Set reminder for: {task}. When: {text}"

    extracted_actions = process_natural_language_reminder(combined_prompt)
    if not extracted_actions:
        green_api_client.send_message(chat_id, "I still couldn't understand the time. Please try again, e.g. 'Tomorrow at 6 PM'.")
        return

    extracted = extracted_actions[0]

    if extracted.get("parsed_datetime_utc"):
        dt         = datetime.fromisoformat(extracted["parsed_datetime_utc"])
        final_task = extracted.get("task_description", task)
        reminder_id = database.add_reminder(chat_id, final_task, dt)
        green_api_client.send_message(
            chat_id,
            f"✅ *Reminder set!*\n"
            f"⏰ *{final_task}*\n"
            f"📅 *{format_datetime_for_user(dt)}*"
        )
        database.update_conversation_state(chat_id, "idle", {"last_actions": [{"type": "reminder", "id": reminder_id}]})
    else:
        green_api_client.send_message(chat_id, "I still couldn't understand the time. Please try again, e.g. 'Tomorrow at 6 PM'.")


# ─────────────────────────────────────────────────────────────────────────────
# TEMP MEDIA + MULTI-MEDIA ATTACHMENT HANDLERS
# ─────────────────────────────────────────────────────────────────────────────

def _offer_text_save(chat_id: str, text: str) -> None:
    """Offers the guided save menu for an unrecognised text message."""
    preview = text[:80] + ("..." if len(text) > 80 else "")
    database.update_conversation_state(chat_id, "awaiting_save_destination", {
        "text_content": text
    })
    green_api_client.send_message(
        chat_id,
        f"💬 I received your message:\n“{preview}”\n\n"
        "What should I do with it?\n"
        "1️⃣ Save as Idea\n"
        "2️⃣ Save as Note\n"
        "3️⃣ Save as Resource\n"
        "4️⃣ Save as Dump\n"
        "5️⃣ Attach to existing element\n"
        "6️⃣ Ignore"
    )


def _handle_incoming_media(chat_id: str, message_data: Dict[str, Any], message_type: str):
    """
    Called when a media message arrives.
    1. Downloads to temp_media/ immediately (Meta URLs are short-lived).
    2. Runs compressor.check_file_size(); rejects oversized audio/video/docs.
    3. Runs compressor.compress_image() if image is over 4.5 MB.
    4. Adds file to the user's current 15-second batch.
    """
    media_type, temp_path, original_name = _extract_and_save_media(
        message_data, message_type, TEMP_MEDIA_DIR, "temp"
    )

    if not media_type or not temp_path:
        green_api_client.send_message(
            chat_id, "⚠️ I couldn't download that file. Please try sending it again."
        )
        return

    abs_path = _resolve_media_path(temp_path)

    # ── Size / compression checks ─────────────────────────────────────────────
    ok, rejection_msg = compressor.check_file_size(abs_path, media_type)
    if not ok:
        green_api_client.send_message(chat_id, rejection_msg)
        try:
            os.remove(abs_path)
        except Exception:
            pass
        return

    if media_type == "image":
        file_size = compressor._file_size(abs_path)
        if file_size > compressor.IMAGE_LIMIT_BYTES:
            logger.info(f"Image {abs_path} is {file_size/1024/1024:.1f} MB — compressing...")
            _, success, err_msg = compressor.compress_image(abs_path)
            if not success:
                green_api_client.send_message(chat_id, err_msg)
                try:
                    os.remove(abs_path)
                except Exception:
                    pass
                return
            logger.info("Image compressed successfully.")

    # ── Add to batch ──────────────────────────────────────────────────────────
    caption = (message_data.get(message_type) or {}).get("caption", "").strip() or None
    existing_batch_id = database.get_open_batch_for_user(chat_id)

    if existing_batch_id:
        temp_id = database.add_temp_media(
            chat_id, temp_path, media_type, original_name,
            caption=caption, batch_id=existing_batch_id
        )
        database.assign_to_batch(temp_id, existing_batch_id)
        logger.info(f"Added file to existing batch {existing_batch_id} for {chat_id}")
    else:
        new_batch_id = str(uuid.uuid4())
        database.add_temp_media(
            chat_id, temp_path, media_type, original_name,
            caption=caption, batch_id=new_batch_id
        )
        logger.info(f"Created new media batch {new_batch_id} for {chat_id}")



def _execute_media_intent(chat_id: str, intent_result: dict,
                          temp_id: int, temp_path: str,
                          media_type: str, original_name: str) -> bool:
    """
    Executes a media routing intent (attach to existing or create new entry).
    Moves the file from temp_media/ to the correct section folder.
    Returns True on success, False on failure.
    """
    intent  = intent_result.get("intent", "unclear")
    section = intent_result.get("section")
    entry_id = intent_result.get("entry_id")
    subject = intent_result.get("subject", original_name or "Media attachment")

    if intent == "discard":
        _discard_temp_media(chat_id, temp_id, temp_path)
        green_api_client.send_message(chat_id, "🗑️ File discarded.")
        return True

    if intent == "unclear" or not section:
        return False

    # ── Attach to existing entry ──────────────────────────────────────────────
    if intent == "attach_to_existing" and entry_id:
        # Move file from temp_media → section folder
        section_dir = SECTION_META.get(section, TEMP_MEDIA_DIR)
        new_path = _move_temp_to_section(temp_path, section_dir)
        if not new_path:
            return False

        database.add_attachment(section, entry_id, chat_id, media_type, new_path, original_name)
        database.delete_temp_media(temp_id)
        database.update_conversation_state(chat_id, "idle", {})

        icon = {"idea": "💡", "note": "📓", "resource": "🔗", "dump": "🗑️"}.get(section, "📎")
        green_api_client.send_message(
            chat_id,
            f"📎 {icon} Attachment added to *{section.capitalize()} #{entry_id}* successfully!"
        )
        return True

    # ── Create new entry ──────────────────────────────────────────────────────
    if intent in ["new_idea", "new_note", "new_resource", "new_dump"]:
        section_dir = SECTION_META.get(section, TEMP_MEDIA_DIR)
        new_path = _move_temp_to_section(temp_path, section_dir)
        if not new_path:
            return False

        icon = {"idea": "💡", "note": "📓", "resource": "🔗", "dump": "🗑️"}.get(section, "📎")
        entry_id_new = _save_new_section_entry(chat_id, section, subject, None, media_type, new_path, original_name)
        database.delete_temp_media(temp_id)
        database.update_conversation_state(chat_id, "idle", {})

        green_api_client.send_message(
            chat_id,
            f"{icon} *{section.capitalize()} #{entry_id_new} saved!*\n"
            f"📌 *Subject:* {subject}\n"
            f"📎 (+ {media_type} attached)"
        )
        return True

    return False


def _move_temp_to_section(temp_path: str, section_dir: str) -> str:
    """
    Moves a file from temp_media to the target section directory.
    Returns the new relative path, or empty string on error.
    """
    abs_temp = _resolve_media_path(temp_path)
    if not os.path.exists(abs_temp):
        logger.error(f"Temp file not found: {abs_temp}")
        return ""

    os.makedirs(section_dir, exist_ok=True)
    filename = os.path.basename(abs_temp)
    dest_abs = os.path.join(section_dir, filename)

    try:
        import shutil
        shutil.move(abs_temp, dest_abs)
        return os.path.relpath(dest_abs, BASE_DIR)
    except Exception as e:
        logger.error(f"Failed to move temp media {abs_temp} -> {dest_abs}: {e}")
        return ""


def _save_new_section_entry(chat_id: str, section: str, subject: str,
                             description, media_type: str, media_path: str,
                             original_name: str) -> int:
    """Creates a new entry in the appropriate section table. Returns the new ID."""
    if section == "idea":
        return database.add_idea(chat_id, subject, description, media_type, media_path, original_name)
    elif section == "note":
        return database.add_note(chat_id, subject, description, media_type, media_path, original_name)
    elif section == "resource":
        return database.add_resource(chat_id, subject, description, media_type, media_path, original_name)
    elif section == "dump":
        return database.add_dump(chat_id, subject, description, media_type, media_path, original_name)
    return 0


def _discard_temp_media(chat_id: str, temp_id: int, temp_path: str):
    """Deletes a temp media file and its DB row."""
    abs_path = _resolve_media_path(temp_path)
    try:
        if os.path.exists(abs_path):
            os.remove(abs_path)
    except Exception as e:
        logger.error(f"Failed to delete temp media file {abs_path}: {e}")
    database.delete_temp_media(temp_id)


def _discard_batch(chat_id: str, batch_id: str):
    """Deletes all files in a batch from disk and removes DB rows."""
    rows = database.get_batch_media(batch_id, chat_id)
    for row in rows:
        abs_path = _resolve_media_path(row["file_path"])
        try:
            if os.path.exists(abs_path):
                os.remove(abs_path)
        except Exception as e:
            logger.error(f"Failed to delete batch file {abs_path}: {e}")
    database.delete_batch_media(batch_id)


def process_media_batch(batch_id: str, user_phone: str, caption: str):
    """
    Called by the scheduler once a batch's 15-second window has closed.
    Presents the guided numbered save menu to the user.
    Caption (if any) is stored in context for later use as description.
    """
    rows = database.get_batch_media(batch_id, user_phone)
    if not rows:
        logger.warning(f"Batch {batch_id} has no rows — skipping")
        return

    file_count  = len(rows)
    file_word   = "file" if file_count == 1 else "files"
    icon_map    = {"image": "🖼️", "video": "🎥", "audio": "🎤", "document": "📄", "sticker": "🙌"}
    file_list   = ", ".join(
        f"{icon_map.get(r['media_type'], '📎')} {r['original_name'] or r['media_type']}"
        for r in rows
    )

    # Store batch_id + any caption as description seed in context
    database.update_conversation_state(user_phone, "awaiting_save_destination", {
        "batch_id":    batch_id,
        "text_caption": caption or "",
    })

    green_api_client.send_message(
        user_phone,
        f"📦 I received *{file_count} {file_word}*:\n{file_list}\n\n"
        "Where should I save them?\n"
        "1️⃣ Idea\n"
        "2️⃣ Note\n"
        "3️⃣ Resource\n"
        "4️⃣ Dump\n"
        "5️⃣ Attach to existing element\n"
        "6️⃣ Discard"
    )


# ──────────────────────────────────────────────────────────────────────────────
# GUIDED SAVE STATES
# ──────────────────────────────────────────────────────────────────────────────

_SECTION_NUMBERS = {"1": "idea", "2": "note", "3": "resource", "4": "dump"}
_SECTION_ICONS   = {"idea": "💡", "note": "📓", "resource": "🔗", "dump": "🗑️"}

_SAVE_MENU = (
    "Where should I save it?\n"
    "1️⃣ Idea\n2️⃣ Note\n3️⃣ Resource\n4️⃣ Dump\n"
    "5️⃣ Attach to existing element\n6️⃣ Discard"
)


def handle_awaiting_save_destination_state(
        chat_id: str, message_data: Dict[str, Any],
        message_type: str, context: Dict[str, Any]):
    """
    User is picking from the numbered save menu (1–6).
    More media arriving in this state is silently added to the batch.
    """
    # If more media arrives, silently accumulate
    if message_type in _SUPPORTED_MEDIA_TYPES:
        _handle_incoming_media(chat_id, message_data, message_type)
        # Update file list in message
        batch_id = context.get("batch_id")
        if batch_id:
            rows = database.get_batch_media(batch_id, chat_id)
            icon_map = {"image": "🖼️", "video": "🎥", "audio": "🎤", "document": "📄"}
            file_list = ", ".join(
                f"{icon_map.get(r['media_type'], '📎')} {r['original_name'] or r['media_type']}"
                for r in rows
            )
            green_api_client.send_message(
                chat_id,
                f"📦 Now {len(rows)} file(s): {file_list}\n\n{_SAVE_MENU}"
            )
        return

    text    = extract_text_from_message(message_data, message_type).strip()
    choice  = text.strip()
    batch_id      = context.get("batch_id")
    text_content  = context.get("text_content", "")
    text_caption  = context.get("text_caption", "")
    description   = text_content or text_caption  # whichever is set

    # ── Discard ───────────────────────────────────────────────────────────
    if choice in ("6", "discard", "ignore", "no"):
        if batch_id:
            _discard_batch(chat_id, batch_id)
        database.update_conversation_state(chat_id, "idle", {})
        green_api_client.send_message(chat_id, "🗑️ Discarded. Nothing was saved.")
        return

    # ── Attach to existing ────────────────────────────────────────────────
    if choice == "5":
        database.update_conversation_state(chat_id, "awaiting_attach_target", {
            "batch_id":     batch_id,
            "text_content": text_content,
        })
        green_api_client.send_message(
            chat_id,
            "Which element should I attach to?\n"
            "Reply with *section + ID*, e.g.:\n"
            "`resource 5`  /  `idea 2`  /  `note 8`"
        )
        return

    # ── New section (1–4) ───────────────────────────────────────────────────
    section = _SECTION_NUMBERS.get(choice)
    if section:
        database.update_conversation_state(chat_id, "awaiting_subject", {
            "batch_id":    batch_id,
            "section":     section,
            "description": description,
        })
        green_api_client.send_message(
            chat_id,
            f"📝 What should be the *subject* for this new {section}?\n"
            f"Reply with a clear, meaningful title."
        )
        return

    # ── Invalid input ───────────────────────────────────────────────────────
    green_api_client.send_message(
        chat_id,
        f"Please reply with a number (1–6):\n{_SAVE_MENU}"
    )


def _is_valid_subject(subject: str) -> bool:
    """Returns True if the subject is a real human-provided title."""
    s = subject.strip()
    if len(s) < 3:
        return False
    if s.isdigit():
        return False
    return True


def handle_awaiting_subject_state(
        chat_id: str, message_data: Dict[str, Any],
        message_type: str, context: Dict[str, Any]):
    """
    User is providing the subject for a new section entry.
    Validates and saves everything once a good subject is received.
    """
    # Allow more media to arrive while typing subject
    if message_type in _SUPPORTED_MEDIA_TYPES:
        _handle_incoming_media(chat_id, message_data, message_type)
        green_api_client.send_message(
            chat_id, "📦 Got another file! 📝 Still waiting for the subject — please reply with a title."
        )
        return

    text    = extract_text_from_message(message_data, message_type).strip()
    batch_id    = context.get("batch_id")
    section     = context.get("section")
    description = context.get("description", "")

    if text.lower() in ("discard", "cancel"):
        if batch_id:
            _discard_batch(chat_id, batch_id)
        database.update_conversation_state(chat_id, "idle", {})
        green_api_client.send_message(chat_id, "🗑️ Cancelled. Nothing was saved.")
        return

    subject = text
    if not _is_valid_subject(subject):
        green_api_client.send_message(
            chat_id,
            "That doesn't look like a clear subject. "
            "Please reply with a proper title (e.g. \"Product launch photos\")\n"
            "Or reply *discard* to cancel."
        )
        return  # keep state, ask again

    # ── Save ──────────────────────────────────────────────────────────────────
    section_dir = SECTION_META.get(section, TEMP_MEDIA_DIR)
    icon        = _SECTION_ICONS.get(section, "📎")
    rows        = database.get_batch_media(batch_id, chat_id) if batch_id else []

    if rows:
        first = rows[0]
        rest  = rows[1:]

        first_path = _move_temp_to_section(first["file_path"], section_dir)
        if not first_path:
            green_api_client.send_message(chat_id, "⚠️ Could not save the primary file. Please try again.")
            database.update_conversation_state(chat_id, "idle", {})
            return

        entry_id = _save_new_section_entry(
            chat_id, section, subject, description or None,
            first["media_type"], first_path, first["original_name"]
        )
        for row in rest:
            new_path = _move_temp_to_section(row["file_path"], section_dir)
            if new_path:
                database.add_attachment(section, entry_id, chat_id, row["media_type"], new_path, row["original_name"])

        database.delete_batch_media(batch_id)
        extra = f" + {len(rest)} more attachment(s)" if rest else ""
        desc_line = f"\n📝 Description saved" if description else ""
        green_api_client.send_message(
            chat_id,
            f"{icon} *{section.capitalize()} #{entry_id} saved!*\n"
            f"📌 *Subject:* {subject}{desc_line}\n"
            f"📎 1 primary file{extra}"
        )
    else:
        # Text-only save (no batch media)
        entry_id = _save_new_section_entry(chat_id, section, subject, description or None, None, None, None)
        green_api_client.send_message(
            chat_id,
            f"{icon} *{section.capitalize()} #{entry_id} saved!*\n"
            f"📌 *Subject:* {subject}" +
            (f"\n📝 *Description:* {description[:60]}{'...' if len(description)>60 else ''}" if description else "")
        )

    database.update_conversation_state(chat_id, "idle", {})


def handle_awaiting_attach_target_state(
        chat_id: str, message_data: Dict[str, Any],
        message_type: str, context: Dict[str, Any]):
    """
    User is providing the target for attachment: "resource 5", "idea 2", etc.
    Validates existence, then attaches files and/or extends description.
    """
    if message_type in _SUPPORTED_MEDIA_TYPES:
        _handle_incoming_media(chat_id, message_data, message_type)
        green_api_client.send_message(
            chat_id, "📦 Got another file! Still waiting — which element should I attach to? (e.g. `resource 5`)"
        )
        return

    text         = extract_text_from_message(message_data, message_type).strip()
    batch_id     = context.get("batch_id")
    text_content = context.get("text_content", "")

    if text.lower() in ("discard", "cancel"):
        if batch_id:
            _discard_batch(chat_id, batch_id)
        database.update_conversation_state(chat_id, "idle", {})
        green_api_client.send_message(chat_id, "🗑️ Cancelled. Nothing was saved.")
        return

    # Parse "section id" — e.g. "resource 5", "idea 2"
    match = re.match(r"^(idea|note|resource|dump)\s+(\d+)$", text.lower().strip())
    if not match:
        green_api_client.send_message(
            chat_id,
            "⚠️ Couldn't parse that. Please reply with *section name + ID*, e.g.:\n"
            "`resource 5`  /  `idea 2`  /  `note 8`  /  `dump 3`\n"
            "Or reply *discard* to cancel."
        )
        return

    section  = match.group(1)
    entry_id = int(match.group(2))
    icon     = _SECTION_ICONS.get(section, "📎")

    # Validate existence
    if not database.entry_exists(section, entry_id):
        green_api_client.send_message(
            chat_id,
            f"⚠️ *{section.capitalize()} #{entry_id}* doesn't exist.\n"
            "Please check the ID and try again, or reply *discard* to cancel."
        )
        return  # keep state so user can retry

    section_dir = SECTION_META.get(section, TEMP_MEDIA_DIR)
    rows        = database.get_batch_media(batch_id, chat_id) if batch_id else []
    actions     = []

    # Attach files
    for row in rows:
        new_path = _move_temp_to_section(row["file_path"], section_dir)
        if new_path:
            database.add_attachment(section, entry_id, chat_id, row["media_type"], new_path, row["original_name"])
            actions.append(f"  📎 {row['original_name'] or row['media_type']}")

    if batch_id:
        database.delete_batch_media(batch_id)

    # Extend description with text
    if text_content:
        ok = database.extend_entry_description(section, entry_id, chat_id, text_content)
        if ok:
            actions.append("  📝 Text appended with extension header")
        else:
            actions.append("  ⚠️ Text could not be appended")

    database.update_conversation_state(chat_id, "idle", {})

    summary = "\n".join(actions) if actions else "  (nothing to attach)"
    green_api_client.send_message(
        chat_id,
        f"{icon} *{section.capitalize()} #{entry_id}* extended:\n{summary}"
    )



def _is_meaningful_subject(subject: str) -> bool:
    """Returns True only if subject is a real human-provided title, not a fallback."""
    s = (subject or "").strip()
    if not s or len(s) < 3:
        return False
    if s.isdigit():                      # just a number like "3"
        return False
    if s.lower().startswith("media batch"):  # our auto-generated fallback
        return False
    return True


def _execute_batch_intent(user_phone: str, intent_result: dict,
                          batch_id: str, rows: list) -> bool:
    """
    Executes an intent against an entire batch.
    - attach_to_existing: verifies entry exists first; if not, asks user to create new
    - new_*: validates subject is meaningful; if not, asks user for a clear subject
    - discard: deletes everything
    Returns True when the request is fully handled (saved OR state set for followup).
    """
    intent   = intent_result.get("intent", "unclear")
    section  = intent_result.get("section")
    entry_id = intent_result.get("entry_id")
    subject  = (intent_result.get("subject") or "").strip()
    icon_map = {"idea": "💡", "note": "📓", "resource": "🔗", "dump": "🗑️"}

    if intent == "discard":
        _discard_batch(user_phone, batch_id)
        database.update_conversation_state(user_phone, "idle", {})
        green_api_client.send_message(user_phone, f"🗑️ All {len(rows)} file(s) discarded.")
        return True

    if intent == "unclear" or not section:
        return False

    section_dir = SECTION_META.get(section, TEMP_MEDIA_DIR)
    icon        = icon_map.get(section, "📎")
    count       = len(rows)

    # ── Attach all files to an existing entry ─────────────────────────────────
    if intent == "attach_to_existing" and entry_id:
        # GUARD: verify the entry actually exists
        if not database.entry_exists(section, entry_id):
            database.update_conversation_state(user_phone, "awaiting_batch_subject", {
                "batch_id": batch_id,
                "section":  section,
            })
            green_api_client.send_message(
                user_phone,
                f"⚠️ *{section.capitalize()} #{entry_id} doesn't exist.*\n\n"
                f"Would you like to create a *new {section}* for these {count} file(s) instead?\n"
                f"Reply with a clear *subject* for it, or *discard* to cancel."
            )
            return True  # handled — waiting for subject reply

        attached = 0
        for row in rows:
            new_path = _move_temp_to_section(row["file_path"], section_dir)
            if new_path:
                database.add_attachment(
                    section, entry_id, user_phone,
                    row["media_type"], new_path, row["original_name"]
                )
                attached += 1
        database.delete_batch_media(batch_id)
        database.update_conversation_state(user_phone, "idle", {})
        green_api_client.send_message(
            user_phone,
            f"📎 {icon} *{attached} attachment(s)* added to *{section.capitalize()} #{entry_id}*!"
        )
        return True

    # ── Create new entry with all files ───────────────────────────────────────
    if intent in ["new_idea", "new_note", "new_resource", "new_dump"]:
        # GUARD: require a clear, meaningful subject
        if not _is_meaningful_subject(subject):
            database.update_conversation_state(user_phone, "awaiting_batch_subject", {
                "batch_id": batch_id,
                "section":  section,
            })
            green_api_client.send_message(
                user_phone,
                f"📝 What should be the *subject* for this new {section}?\n"
                f"Please reply with a clear, meaningful title.\n"
                f"(Or reply *discard* to cancel.)"
            )
            return True  # handled — waiting for subject reply

        first = rows[0]
        rest  = rows[1:]

        first_path = _move_temp_to_section(first["file_path"], section_dir)
        if not first_path:
            return False

        entry_id_new = _save_new_section_entry(
            user_phone, section, subject, None,
            first["media_type"], first_path, first["original_name"]
        )

        for row in rest:
            new_path = _move_temp_to_section(row["file_path"], section_dir)
            if new_path:
                database.add_attachment(
                    section, entry_id_new, user_phone,
                    row["media_type"], new_path, row["original_name"]
                )

        database.delete_batch_media(batch_id)
        database.update_conversation_state(user_phone, "idle", {})

        extra = f" + {len(rest)} more attachment(s)" if rest else ""
        green_api_client.send_message(
            user_phone,
            f"{icon} *{section.capitalize()} #{entry_id_new} saved!*\n"
            f"📌 *Subject:* {subject}\n"
            f"📎 1 primary media{extra}"
        )
        return True

    return False


def handle_awaiting_batch_subject_state(chat_id: str, message_data: Dict[str, Any],
                                        message_type: str, context: Dict[str, Any]):
    """
    User replied with a subject for a new section entry (after bot asked for one).
    Creates the entry and saves all batch files.
    """
    text     = extract_text_from_message(message_data, message_type).strip()
    batch_id = context.get("batch_id")
    section  = context.get("section")
    icon_map = {"idea": "💡", "note": "📓", "resource": "🔗", "dump": "🗑️"}
    icon     = icon_map.get(section, "📎")

    if not batch_id or not section:
        database.update_conversation_state(chat_id, "idle", {})
        return

    # Discard command
    if text.lower() in ["discard", "cancel", "no", "nope"]:
        _discard_batch(chat_id, batch_id)
        database.update_conversation_state(chat_id, "idle", {})
        green_api_client.send_message(chat_id, "🗑️ Files discarded. Nothing was saved.")
        return

    subject = text.strip()
    if not _is_meaningful_subject(subject):
        green_api_client.send_message(
            chat_id,
            "That doesn't look like a clear subject. "
            "Please reply with a proper title (e.g. 'Product launch photos')\n"
            "Or reply *discard* to cancel."
        )
        return  # keep state, ask again

    rows = database.get_batch_media(batch_id, chat_id)
    if not rows:
        green_api_client.send_message(chat_id, "⚠️ Your files seem to have been discarded already.")
        database.update_conversation_state(chat_id, "idle", {})
        return

    section_dir = SECTION_META.get(section, TEMP_MEDIA_DIR)
    first = rows[0]
    rest  = rows[1:]

    first_path = _move_temp_to_section(first["file_path"], section_dir)
    if not first_path:
        green_api_client.send_message(chat_id, "⚠️ Could not save the file. Please try again.")
        database.update_conversation_state(chat_id, "idle", {})
        return

    entry_id_new = _save_new_section_entry(
        chat_id, section, subject, None,
        first["media_type"], first_path, first["original_name"]
    )

    for row in rest:
        new_path = _move_temp_to_section(row["file_path"], section_dir)
        if new_path:
            database.add_attachment(
                section, entry_id_new, chat_id,
                row["media_type"], new_path, row["original_name"]
            )

    database.delete_batch_media(batch_id)
    database.update_conversation_state(chat_id, "idle", {})

    extra = f" + {len(rest)} more attachment(s)" if rest else ""
    green_api_client.send_message(
        chat_id,
        f"{icon} *{section.capitalize()} #{entry_id_new} saved!*\n"
        f"📌 *Subject:* {subject}\n"
        f"📎 1 primary media{extra}"
    )



def handle_awaiting_media_intent_state(chat_id: str, message_data: Dict[str, Any],
                                        message_type: str, context: Dict[str, Any]):
    """
    User replied to the 'what should I do with your files?' prompt.
    Works on a whole batch (batch_id in context) or a single file (temp_id, legacy).
    """
    # If more media arrives while waiting — add it to the batch silently
    if message_type in ("image", "video", "audio", "document", "sticker"):
        _handle_incoming_media(chat_id, message_data, message_type)
        return

    text       = extract_text_from_message(message_data, message_type).strip()
    batch_id   = context.get("batch_id")
    temp_id    = context.get("temp_id")   # legacy single-file fallback

    # ── Handle batch ──────────────────────────────────────────────────────────
    if batch_id:
        rows = database.get_batch_media(batch_id, chat_id)
        if not rows:
            green_api_client.send_message(chat_id, "⚠️ Could not find your pending files. They may have been discarded.")
            database.update_conversation_state(chat_id, "idle", {})
            return

        result     = process_media_caption(text)
        confidence = result.get("confidence", "low")

        if confidence == "high":
            success = _execute_batch_intent(chat_id, result, batch_id, rows)
            if success:
                return

        elif confidence == "medium":
            section  = result.get("section", "unknown")
            entry_id = result.get("entry_id")
            count    = len(rows)
            if entry_id:
                msg = f"I think you want to attach all {count} file(s) to *{section} #{entry_id}*. Is that right? Reply *YES* or *NO*."
            else:
                msg = f"I think you want to save all {count} file(s) as a new *{section}*. Is that right? Reply *YES* or *NO*."
            green_api_client.send_message(chat_id, msg)
            database.update_conversation_state(chat_id, "awaiting_media_intent", {
                "batch_id": batch_id,
                "pending_result": result
            })
            return

        # Low confidence — ask again
        green_api_client.send_message(
            chat_id,
            "I couldn't understand that. Please reply with:\n"
            "- *new idea* / *new note* / *new resource* / *new dump*\n"
            "- *attach to idea 3* (or any section + ID)\n"
            "- *discard* to delete all files"
        )
        return

    # ── Legacy single-file fallback ───────────────────────────────────────────
    if not temp_id:
        database.update_conversation_state(chat_id, "idle", {})
        return

    temp_row = database.get_temp_media_by_id(temp_id, chat_id)
    if not temp_row:
        green_api_client.send_message(chat_id, "⚠️ Could not find your pending file. It may have already been discarded.")
        database.update_conversation_state(chat_id, "idle", {})
        return

    temp_path     = temp_row["file_path"]
    media_type    = temp_row["media_type"]
    original_name = temp_row["original_name"] or ""

    result     = process_media_caption(text)
    confidence = result.get("confidence", "low")

    if confidence == "high":
        success = _execute_media_intent(chat_id, result, temp_id, temp_path, media_type, original_name)
        if success:
            return

    elif confidence == "medium":
        section  = result.get("section", "unknown")
        entry_id = result.get("entry_id")
        msg = (
            f"I think you want to attach this to *{section} #{entry_id}*." if entry_id
            else f"I think you want to save this as a new *{section}*."
        ) + " Is that right? Reply *YES* or *NO*."
        green_api_client.send_message(chat_id, msg)
        database.update_conversation_state(chat_id, "awaiting_media_intent", {
            "temp_id": temp_id, "pending_result": result
        })
        return

    green_api_client.send_message(
        chat_id,
        "I couldn't understand that. Please reply with:\n"
        "- *new idea* / *new note* / *new resource* / *new dump*\n"
        "- *attach to idea 3* (or any section + ID)\n"
        "- *discard* to delete the file"
    )


def handle_awaiting_section_confirmation_state(chat_id: str, message_data: Dict[str, Any],
                                                message_type: str, context: Dict[str, Any]):
    """
    Handles YES/NO response when bot had medium-confidence section detection.
    """
    text = extract_text_from_message(message_data, message_type).lower().strip()
    section     = context.get("section", "")
    subject     = context.get("subject", "")
    description = context.get("description", "")
    temp_id     = context.get("temp_media_id")

    YES = ["yes", "y", "yeah", "yep", "correct", "sure", "ok", "okay"]
    NO  = ["no", "n", "nope", "incorrect", "cancel", "wrong"]

    if text in YES:
        # Save the entry (no inline media since this came from a text message)
        entry_id = _save_new_section_entry(chat_id, section, subject, description, None, None, None)
        icon = {"idea": "💡", "note": "📓", "resource": "🔗", "dump": "🗑️"}.get(section, "📎")

        # If there was a pending temp media, attach it now
        if temp_id:
            temp_row = database.get_temp_media_by_id(temp_id, chat_id)
            if temp_row:
                section_dir = SECTION_META.get(section, TEMP_MEDIA_DIR)
                new_path = _move_temp_to_section(temp_row["file_path"], section_dir)
                if new_path:
                    database.add_attachment(section, entry_id, chat_id,
                                            temp_row["media_type"], new_path,
                                            temp_row["original_name"])
                    database.delete_temp_media(temp_id)

        green_api_client.send_message(
            chat_id,
            f"{icon} *{section.capitalize()} #{entry_id} saved!*\n📌 *Subject:* {subject}"
            + (f"\n📝 *Description:* {description}" if description else "")
        )
        database.update_conversation_state(chat_id, "idle", {})

    elif text in NO:
        green_api_client.send_message(
            chat_id,
            "Got it — not saved. What would you like to do with this message?\n"
            "Say 'new idea', 'new note', 'new resource', 'new dump', or just ignore it."
        )
        database.update_conversation_state(chat_id, "idle", {})

    else:
        green_api_client.send_message(chat_id, "Please reply *YES* or *NO*.")




# ─────────────────────────────────────────────────────────────────────────────
# IDEA STORE HANDLERS
# ─────────────────────────────────────────────────────────────────────────────

def _save_idea_media(message_data: Dict[str, Any], message_type: str):
    """Downloads media from a WhatsApp message and saves it to ideas_media/."""
    return _extract_and_save_media(message_data, message_type, IDEA_MEDIA_DIR, "idea")


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
    """Retrieves and sends a single idea back to the user, including any media and extra attachments."""
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

    # Send primary media
    if media_type and media_path:
        abs_path = _resolve_media_path(media_path)
        if os.path.exists(abs_path):
            try:
                green_api_client.send_file(chat_id, abs_path, media_original_name)
            except Exception as e:
                logger.error(f"Failed to send idea media for idea #{idea_id}: {e}")

    # Send extra attachments
    attachments = database.get_attachments("idea", idea_id, chat_id)
    for att in attachments:
        abs_path = _resolve_media_path(att["media_path"])
        if os.path.exists(abs_path):
            try:
                green_api_client.send_file(chat_id, abs_path, att["original_name"] or "attachment")
            except Exception as e:
                logger.error(f"Failed to send attachment for idea #{idea_id}: {e}")
    if attachments:
        green_api_client.send_message(chat_id, f"📎 {len(attachments)} extra attachment(s) shown above.")


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
    """Downloads media from a WhatsApp message and saves it to notes_media/."""
    return _extract_and_save_media(message_data, message_type, NOTE_MEDIA_DIR, "note")


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
    """Retrieves and sends a single note back to the user, including media and attachments."""
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

    if media_type and media_path:
        abs_path = _resolve_media_path(media_path)
        if os.path.exists(abs_path):
            try:
                green_api_client.send_file(chat_id, abs_path, media_original_name)
            except Exception as e:
                logger.error(f"Failed to send note media for note #{note_id}: {e}")

    attachments = database.get_attachments("note", note_id, chat_id)
    for att in attachments:
        abs_path = _resolve_media_path(att["media_path"])
        if os.path.exists(abs_path):
            try:
                green_api_client.send_file(chat_id, abs_path, att["original_name"] or "attachment")
            except Exception as e:
                logger.error(f"Failed to send attachment for note #{note_id}: {e}")
    if attachments:
        green_api_client.send_message(chat_id, f"📎 {len(attachments)} extra attachment(s) shown above.")


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
    """Downloads media from a WhatsApp message and saves it to resources_media/."""
    return _extract_and_save_media(message_data, message_type, RESOURCE_MEDIA_DIR, "resource")

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
    if item['media_type'] and item['media_path']:
        abs_path = _resolve_media_path(item['media_path'])
        if os.path.exists(abs_path):
            try: green_api_client.send_file(chat_id, abs_path, item['media_original_name'] or "attachment")
            except: pass
    attachments = database.get_attachments("resource", r_id, chat_id)
    for att in attachments:
        abs_path = _resolve_media_path(att["media_path"])
        if os.path.exists(abs_path):
            try: green_api_client.send_file(chat_id, abs_path, att["original_name"] or "attachment")
            except: pass
    if attachments:
        green_api_client.send_message(chat_id, f"📎 {len(attachments)} extra attachment(s) shown above.")

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
    """Downloads media from a WhatsApp message and saves it to dumps_media/."""
    return _extract_and_save_media(message_data, message_type, DUMP_MEDIA_DIR, "dump")

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
    if item['media_type'] and item['media_path']:
        abs_path = _resolve_media_path(item['media_path'])
        if os.path.exists(abs_path):
            try: green_api_client.send_file(chat_id, abs_path, item['media_original_name'] or "attachment")
            except: pass
    attachments = database.get_attachments("dump", d_id, chat_id)
    for att in attachments:
        abs_path = _resolve_media_path(att["media_path"])
        if os.path.exists(abs_path):
            try: green_api_client.send_file(chat_id, abs_path, att["original_name"] or "attachment")
            except: pass
    if attachments:
        green_api_client.send_message(chat_id, f"📎 {len(attachments)} extra attachment(s) shown above.")

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
