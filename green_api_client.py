import requests
import logging
import os
from typing import Optional, Dict, Any
import time

import config

logger = logging.getLogger(__name__)

def get_base_url() -> str:
    return f"https://api.green-api.com/waInstance{config.GREEN_API_INSTANCE_ID}"

def send_message(chat_id: str, message: str) -> bool:
    """Sends a text message to a specific WhatsApp chat using Green API."""
    url = f"{get_base_url()}/sendMessage/{config.GREEN_API_TOKEN}"
    payload = {
        "chatId": chat_id,
        "message": message
    }
    
    # Retry logic
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.post(url, json=payload, timeout=10)
            response.raise_for_status()
            logger.info(f"Message sent successfully to {chat_id}")
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to send message to {chat_id}, attempt {attempt + 1}/{max_retries}: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
            
    return False

def download_file(url: str, file_path: str) -> bool:
    """Downloads a file from a Green API URL (for voice messages)."""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.get(url, stream=True, timeout=30)
            response.raise_for_status()
            
            # Create directory if it doesn't exist
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            
            with open(file_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
                    
            logger.info(f"File downloaded successfully to {file_path}")
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to download file, attempt {attempt + 1}/{max_retries}: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
                
    return False


def send_file(chat_id: str, file_path: str, file_name: str) -> bool:
    """Sends a local file (image, audio, video) to a WhatsApp chat via Green API's sendFileByUpload endpoint."""
    url = f"{get_base_url()}/sendFileByUpload/{config.GREEN_API_TOKEN}"
    
    if not os.path.exists(file_path):
        logger.error(f"send_file: file not found at {file_path}")
        return False

    max_retries = 3
    for attempt in range(max_retries):
        try:
            with open(file_path, "rb") as f:
                files = {"file": (file_name, f)}
                data = {"chatId": chat_id, "fileName": file_name}
                response = requests.post(url, data=data, files=files, timeout=60)
                response.raise_for_status()
                logger.info(f"File sent successfully to {chat_id}: {file_name}")
                return True
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to send file to {chat_id}, attempt {attempt + 1}/{max_retries}: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)

    return False

def set_webhook(webhook_url: str) -> bool:
    """Sets the webhook URL for receiving incoming messages."""
    url = f"{get_base_url()}/setSettings/{config.GREEN_API_TOKEN}"
    payload = {
        "webhookUrl": webhook_url,
        "outgoingWebhook": "yes",
        "stateWebhook": "yes",
        "incomingWebhook": "yes"
    }
    
    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        logger.info(f"Webhook set successfully to {webhook_url}")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to set webhook: {e}")
        return False
