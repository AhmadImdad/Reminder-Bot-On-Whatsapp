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
database.seed_admin_phone()  # Ensure admin phone is always in the allowed_users table

# Initialize Scheduler
scheduler = start_scheduler()

# Flask App
app = Flask(__name__)


# ── Meta webhook verification handshake (GET) ─────────────────────────────────
@app.route('/webhook', methods=['GET'])
def webhook_verify():
    """
    Meta sends a GET request to verify the webhook endpoint before activating it.

    Required query params:
      hub.mode         — must equal 'subscribe'
      hub.verify_token — must match META_WEBHOOK_VERIFY_TOKEN in .env
      hub.challenge    — an arbitrary string that must be echoed back
    """
    mode      = request.args.get("hub.mode")
    token     = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == config.META_WEBHOOK_VERIFY_TOKEN:
        logger.info("Webhook verified successfully by Meta.")
        return challenge, 200

    logger.warning(f"Webhook verification failed. mode={mode}, token={token}")
    return jsonify({"status": "error", "message": "Verification failed"}), 403


# ── Incoming message webhook (POST) ───────────────────────────────────────────
@app.route('/webhook', methods=['POST'])
def webhook():
    """Endpoint to receive incoming messages from Meta WhatsApp Cloud API."""
    data = request.json
    if not data:
        return jsonify({"status": "error", "message": "No JSON payload provided"}), 400

    logger.info(f"Received webhook: object={data.get('object')}")

    # Process asynchronously to return 200 OK immediately to Meta
    # (Meta will retry if we don't respond within 20 seconds)
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
