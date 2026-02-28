from flask import Flask, request, jsonify
import logging
import threading

import config
import database
from message_handler import handle_incoming_webhook
from reminder_scheduler import start_scheduler
from utils import setup_logging

# Initialize tools
setup_logging()
logger = logging.getLogger(__name__)

# Initialize DB on startup
database.init_db()

# Initialize Scheduler
scheduler = start_scheduler()

# Flask App
app = Flask(__name__)

@app.route('/webhook', methods=['POST'])
def webhook():
    """Endpoint to receive incoming messages from Green API."""
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "No JSON payload provided"}), 400
        
    logger.info(f"Received webhook: {data.get('typeWebhook')}")
    
    # Process asynchronously to return 200 OK immediately to Green API
    # and prevent timeout retries
    threading.Thread(target=handle_incoming_webhook, args=(data,)).start()
    
    return jsonify({"status": "success", "message": "Webhook received"}), 200

@app.route('/health', methods=['GET'])
def health_check():
    """Endpoint for monitoring to check if the bot is running."""
    return jsonify({"status": "running", "service": "WhatsApp Reminder Bot"}), 200

if __name__ == '__main__':
    logger.info(f"Starting webhook server on port {config.WEBHOOK_PORT}...")
    # Recommend running with Gunicorn in production
    app.run(host='0.0.0.0', port=config.WEBHOOK_PORT, debug=False)
