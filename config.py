import os
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

# ── Meta WhatsApp Business Cloud API ─────────────────────────────────────────
# Permanent System User access token from Meta Business Settings.
META_ACCESS_TOKEN: str = os.getenv("META_ACCESS_TOKEN", "")

# Phone Number ID shown in Meta Developer Dashboard → WhatsApp → API Setup.
META_PHONE_NUMBER_ID: str = os.getenv("META_PHONE_NUMBER_ID", "")

# WhatsApp Business Account ID (same page as phone number ID).
META_WABA_ID: str = os.getenv("META_WABA_ID", "")

# A secret string you choose — must match what you enter in the Meta Dashboard
# webhook verification form.
META_WEBHOOK_VERIFY_TOKEN: str = os.getenv("META_WEBHOOK_VERIFY_TOKEN", "")

# Graph API version — update when Meta releases a newer stable version.
META_API_VERSION: str = os.getenv("META_API_VERSION", "v20.0")

# ── Groq API (still used for Whisper audio transcription) ────────────────────
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")

# ── Gemini API ────────────────────────────────────────────────────────────────
GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")

# ── Webhook ───────────────────────────────────────────────────────────────────
WEBHOOK_PORT: int = int(os.getenv("WEBHOOK_PORT", "5000"))

# ── Timezone ──────────────────────────────────────────────────────────────────
TIMEZONE: str = os.getenv("TIMEZONE", "Asia/Kolkata")

# ── Database & Logging ────────────────────────────────────────────────────────
DB_PATH: str = os.getenv("DB_PATH", "reminder_bot.db")
LOG_PATH: str = os.getenv("LOG_PATH", "app.log")

# ── Optional settings ─────────────────────────────────────────────────────────
MAX_MESSAGES_PER_HOUR: int = int(os.getenv("MAX_MESSAGES_PER_HOUR", "50"))
ALLOWED_PHONE_NUMBER: str = os.getenv("ALLOWED_PHONE_NUMBER", "923066008613")
