# Dashboard Settings
PAGE_TITLE = "WhatsApp Reminder Bot"
PAGE_ICON = "🤖"
LAYOUT = "wide"
SIDEBAR_STATE = "expanded"

# Session settings
SESSION_TIMEOUT = 30  # minutes
AUTO_REFRESH_INTERVAL = 60  # seconds

# Database paths (Relative to frontend dir)
import os
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "reminder_bot.db")
AUTH_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "auth.yaml")

# Check if Backend Config Exists to read settings
import sys
sys.path.append(BASE_DIR)
try:
    import config as backend_config
    ALLOWED_PHONE_NUMBER = getattr(backend_config, "ALLOWED_PHONE_NUMBER", "")
except ImportError:
    ALLOWED_PHONE_NUMBER = ""

# Pagination
ITEMS_PER_PAGE = 20

# Colors
COLOR_SUCCESS = "#28a745"
COLOR_WARNING = "#ffc107"
COLOR_DANGER = "#dc3545"
COLOR_INFO = "#17a2b8"
