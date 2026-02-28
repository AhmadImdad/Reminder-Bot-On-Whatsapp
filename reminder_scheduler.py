import logging
from apscheduler.schedulers.background import BackgroundScheduler
from datetime import datetime, timedelta

import database
import green_api_client

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

def start_scheduler() -> BackgroundScheduler:
    """Initializes and starts the APScheduler."""
    scheduler = BackgroundScheduler()
    # Check every minute
    scheduler.add_job(check_and_send_reminders, 'interval', minutes=1)
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
