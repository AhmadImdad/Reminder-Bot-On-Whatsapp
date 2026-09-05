import logging
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta

import database
import meta_api_client as green_api_client  # drop-in replacement for Green API

logger = logging.getLogger(__name__)

def check_and_send_reminders():
    """Checks the database for pending reminders that are due and sends them."""
    now = datetime.utcnow()
    # Add a 1-minute buffer to ensure we catch reminders right on the minute
    buffer_time = now + timedelta(minutes=1)
    
    logger.debug(f"Checking for reminders due before {buffer_time}")
    
    pending_reminders = database.get_pending_reminders(buffer_time)
    
    for row in pending_reminders:
        reminder_id = row['id']
        user_phone = row['user_phone']
        task = row['task']
        
        logger.info(f"Triggering reminder [{reminder_id}] for {user_phone}: {task}")
        
        message = f"⏰ **Reminder:** {task}"
        success = green_api_client.send_message(user_phone, message)
        
        if success:
            database.mark_reminder_completed(reminder_id)
        else:
            logger.error(f"Failed to send reminder [{reminder_id}]. Will retry next cycle.")

def warn_expiring_temp_media():
    """Sends a warning to users whose temp media has been sitting for 60 minutes."""
    rows = database.get_expiring_temp_media()
    for row in rows:
        user_phone = row['user_phone']
        logger.info(f"Sending temp media expiry warning to {user_phone}")
        try:
            green_api_client.send_message(
                user_phone,
                "⏳ *Pending File Alert*\n\n"
                "You have a media file that was saved 1 hour ago but hasn't been assigned anywhere.\n\n"
                "Reply within *10 minutes* to keep it:\n"
                "- 'save as idea' / 'save as note' / 'save as resource' / 'save as dump'\n"
                "- 'attach to resource 3' (or any section + ID)\n"
                "- 'discard' to delete it now\n\n"
                "If you don't reply, the file will be automatically deleted."
            )
            database.mark_temp_media_warning_sent(row['id'])
        except Exception as e:
            logger.error(f"Failed to send temp media warning to {user_phone}: {e}")


def discard_expired_temp_media():
    """Deletes temp media files that have passed their 10-minute grace period."""
    import os
    import config
    rows = database.get_expired_temp_media()
    for row in rows:
        user_phone = row['user_phone']
        file_path = row['file_path']
        row_id = row['id']

        # Resolve absolute path
        abs_path = os.path.join(config.BASE_DIR, file_path) if not os.path.isabs(file_path) else file_path

        logger.info(f"Discarding expired temp media {abs_path} for {user_phone}")

        # Delete the file
        try:
            if os.path.exists(abs_path):
                os.remove(abs_path)
        except Exception as e:
            logger.error(f"Failed to delete temp media file {abs_path}: {e}")

        # Remove DB row
        database.delete_temp_media(row_id)

        # Notify user
        try:
            green_api_client.send_message(
                user_phone,
                "🗑️ Your pending media file has been *automatically discarded* "
                "(no response received within 10 minutes)."
            )
        except Exception as e:
            logger.error(f"Failed to send discard notification to {user_phone}: {e}")


def start_scheduler() -> BackgroundScheduler:
    """Initializes and starts the APScheduler."""
    scheduler = BackgroundScheduler()
    # Check every minute for due reminders
    scheduler.add_job(check_and_send_reminders, 'interval', minutes=1)
    # Check every 5 minutes for temp media approaching expiry
    scheduler.add_job(warn_expiring_temp_media, 'interval', minutes=5)
    # Check every 2 minutes for expired temp media to discard
    scheduler.add_job(discard_expired_temp_media, 'interval', minutes=2)
    scheduler.start()
    logger.info("Reminder scheduler started.")
    return scheduler

if __name__ == "__main__":
    # For independent testing
    import time
    from utils import setup_logging
    
    setup_logging()
    database.init_db()
    
    scheduler = start_scheduler()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        scheduler.shutdown()
