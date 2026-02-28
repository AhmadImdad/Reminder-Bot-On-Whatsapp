import os
from typing import Optional
from dotenv import load_dotenv

load_dotenv()

# Green API
GREEN_API_INSTANCE_ID: str = os.getenv("GREEN_API_INSTANCE_ID", "")
GREEN_API_TOKEN: str = os.getenv("GREEN_API_TOKEN", "")

# Groq API
GROQ_API_KEY: str = os.getenv("GROQ_API_KEY", "")

# Webhook
WEBHOOK_PORT: int = int(os.getenv("WEBHOOK_PORT", "5000"))

# Timezone
TIMEZONE: str = os.getenv("TIMEZONE", "Asia/Kolkata")

# Database & Logging
DB_PATH: str = os.getenv("DB_PATH", "reminder_bot.db")
LOG_PATH: str = os.getenv("LOG_PATH", "app.log")

# Optional settings
MAX_MESSAGES_PER_HOUR: int = int(os.getenv("MAX_MESSAGES_PER_HOUR", "50"))
ALLOWED_PHONE_NUMBER: str = os.getenv("ALLOWED_PHONE_NUMBER", "923066008613")
