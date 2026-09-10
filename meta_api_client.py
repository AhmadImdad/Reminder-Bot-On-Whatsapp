import requests
import logging
import os
import time
from typing import Optional

import config

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _phone(chat_id: str) -> str:
    """
    Normalise a phone/chat identifier to a plain E.164 number string.

    Green API uses the format '923066008613@c.us'.
    Meta Cloud API expects just '923066008613'.
    This helper strips the suffix so both formats are accepted.
    """
    return chat_id.split("@")[0]


def _graph_url(path: str) -> str:
    """Build a fully-qualified Graph API URL."""
    return f"https://graph.facebook.com/{config.META_API_VERSION}/{path}"


def _auth_headers() -> dict:
    return {"Authorization": f"Bearer {config.META_ACCESS_TOKEN}"}


# ---------------------------------------------------------------------------
# Send text message
# ---------------------------------------------------------------------------

def _split_message(text: str, max_length: int = 4000) -> list:
    """Splits a long message into chunks under max_length, preferring newline boundaries."""
    if len(text) <= max_length:
        return [text]

    chunks = []
    lines = text.split("\n")
    current_chunk = []
    current_length = 0

    for line in lines:
        line_len = len(line) + 1  # include newline
        if current_length + line_len > max_length:
            if current_chunk:
                chunks.append("\n".join(current_chunk))
                current_chunk = []
                current_length = 0
            # If a single line itself is longer than max_length, hard-split it
            while len(line) > max_length:
                chunks.append(line[:max_length])
                line = line[max_length:]
            if line:
                current_chunk.append(line)
                current_length = len(line) + 1
        else:
            current_chunk.append(line)
            current_length += line_len

    if current_chunk:
        chunks.append("\n".join(current_chunk))

    return chunks


def _send_single_message(chat_id: str, message: str) -> bool:
    """Sends a single message chunk (<= 4096 characters) via Meta Cloud API."""
    phone = _phone(chat_id)
    url = _graph_url(f"{config.META_PHONE_NUMBER_ID}/messages")
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": phone,
        "type": "text",
        "text": {"preview_url": False, "body": message},
    }

    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.post(url, json=payload, headers=_auth_headers(), timeout=10)
            response.raise_for_status()
            logger.info(f"Message sent successfully to {phone}")
            return True
        except requests.exceptions.RequestException as e:
            err_body = f" | Response: {e.response.text}" if getattr(e, "response", None) is not None else ""
            logger.error(
                f"Failed to send message to {phone}, "
                f"attempt {attempt + 1}/{max_retries}: {e}{err_body}"
            )
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # Exponential back-off

    return False


def send_message(chat_id: str, message: str) -> bool:
    """
    Send a plain-text WhatsApp message via Meta Cloud API.
    Automatically chunks messages exceeding WhatsApp's 4096 character limit.

    Args:
        chat_id: Recipient phone number, either as '923066008613' or
                 '923066008613@c.us' (the @c.us suffix is stripped automatically).
        message: The text body to send.

    Returns:
        True on success, False after all retries are exhausted.
    """
    if not message:
        return True

    # If message exceeds WhatsApp's limit, split into parts
    if len(message) > 4000:
        chunks = _split_message(message, max_length=4000)
        all_ok = True
        for chunk in chunks:
            if not _send_single_message(chat_id, chunk):
                all_ok = False
            time.sleep(0.3)
        return all_ok

    return _send_single_message(chat_id, message)


# ---------------------------------------------------------------------------
# Download media (replaces green_api_client.download_file)
# ---------------------------------------------------------------------------

def download_file(url_or_media_id: str, file_path: str) -> bool:
    """
    Download a media file from Meta's servers.

    Meta webhooks give you a ``media_id`` (e.g. '123456789') rather than a
    direct download URL.  This function accepts **both** a raw media_id and a
    full https URL so that any existing call-sites work without changes.

    Steps when given a media_id:
      1. GET /{media_id} → resolves to a short-lived download URL.
      2. GET that URL (with Bearer token) → streams the file to disk.

    Args:
        url_or_media_id: Either a plain media_id string or a full HTTPS URL.
        file_path: Absolute path where the file should be written.

    Returns:
        True on success, False after all retries are exhausted.
    """
    max_retries = 3

    # Step 1 – resolve media_id → download URL (skip if already a full URL)
    if url_or_media_id.startswith("http"):
        download_url = url_or_media_id
    else:
        media_id = url_or_media_id
        resolve_url = _graph_url(media_id)
        try:
            resp = requests.get(resolve_url, headers=_auth_headers(), timeout=10)
            resp.raise_for_status()
            download_url = resp.json().get("url", "")
            if not download_url:
                logger.error(f"Meta API returned no URL for media_id={media_id}")
                return False
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to resolve media_id={media_id}: {e}")
            return False

    # Step 2 – stream the file to disk (needs Bearer token for Meta URLs)
    for attempt in range(max_retries):
        try:
            response = requests.get(
                download_url,
                headers=_auth_headers(),
                stream=True,
                timeout=30,
            )
            response.raise_for_status()

            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            logger.info(f"File downloaded successfully to {file_path}")
            return True
        except requests.exceptions.RequestException as e:
            logger.error(f"Failed to download file, attempt {attempt + 1}/{max_retries}: {e}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)

    return False


# ---------------------------------------------------------------------------
# Send file / media
# ---------------------------------------------------------------------------

def _upload_media(file_path: str, mime_type: str) -> Optional[str]:
    """
    Upload a local file to Meta's media store and return its media_id.

    Returns None on failure.
    """
    url = _graph_url(f"{config.META_PHONE_NUMBER_ID}/media")
    params = {"messaging_product": "whatsapp"}

    try:
        with open(file_path, "rb") as f:
            files = {"file": (os.path.basename(file_path), f, mime_type)}
            response = requests.post(
                url, params=params, headers=_auth_headers(), files=files, timeout=60
            )
        response.raise_for_status()
        media_id = response.json().get("id")
        logger.info(f"Uploaded media, got media_id={media_id}")
        return media_id
    except requests.exceptions.RequestException as e:
        err_body = f" | Response: {e.response.text}" if getattr(e, "response", None) is not None else ""
        logger.error(f"Failed to upload media {file_path}: {e}{err_body}")
        return None


def _guess_mime_type(file_path: str) -> str:
    """Return a best-guess MIME type based on file extension."""
    ext = os.path.splitext(file_path)[1].lower()
    mime_map = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".mp4": "video/mp4",
        ".ogg": "audio/ogg",
        ".mp3": "audio/mpeg",
        ".aac": "audio/aac",
        ".pdf": "application/pdf",
        ".doc": "application/msword",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }
    return mime_map.get(ext, "application/octet-stream")


def _meta_media_type(file_path: str) -> str:
    """Map a file extension to a Meta message type ('image', 'audio', 'video', 'document')."""
    ext = os.path.splitext(file_path)[1].lower()
    if ext in {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
        return "image"
    if ext in {".ogg", ".mp3", ".aac", ".m4a"}:
        return "audio"
    if ext in {".mp4", ".3gp"}:
        return "video"
    return "document"


def send_file(chat_id: str, file_path: str, file_name: str) -> bool:
    """
    Send a local media file to a WhatsApp contact via Meta Cloud API.

    The file is first uploaded to Meta's media store to obtain a media_id,
    which is then referenced in the message payload.

    Args:
        chat_id: Recipient phone (accepts '923...' or '923...@c.us' format).
        file_path: Absolute path to the local file.
        file_name: Display filename (used as caption/filename in Meta payload).

    Returns:
        True on success, False on failure.
    """
    if not os.path.exists(file_path):
        logger.error(f"send_file: file not found at {file_path}")
        return False

    phone = _phone(chat_id)
    mime_type = _guess_mime_type(file_path)
    meta_type = _meta_media_type(file_path)

    # Step 1: upload to get media_id
    media_id = _upload_media(file_path, mime_type)
    if not media_id:
        logger.error(f"send_file: upload failed for {file_path}")
        return False

    # Step 2: send a message referencing the media_id
    url = _graph_url(f"{config.META_PHONE_NUMBER_ID}/messages")

    if meta_type == "document":
        media_payload = {"id": media_id, "filename": file_name}
    else:
        media_payload = {"id": media_id}

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": phone,
        "type": meta_type,
        meta_type: media_payload,
    }

    max_retries = 3
    for attempt in range(max_retries):
        try:
            response = requests.post(url, json=payload, headers=_auth_headers(), timeout=30)
            response.raise_for_status()
            logger.info(f"File sent successfully to {phone}: {file_name}")
            return True
        except requests.exceptions.RequestException as e:
            err_body = f" | Response: {e.response.text}" if getattr(e, "response", None) is not None else ""
            logger.error(
                f"Failed to send file to {phone}, "
                f"attempt {attempt + 1}/{max_retries}: {e}{err_body}"
            )
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)

    return False
