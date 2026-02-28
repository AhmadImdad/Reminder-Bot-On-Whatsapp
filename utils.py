import logging
import pytz
from datetime import datetime
import config

def setup_logging():
    """Sets up application logging."""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        handlers=[
            logging.FileHandler(config.LOG_PATH),
            logging.StreamHandler()
        ]
    )

def local_to_utc(dt: datetime) -> datetime:
    """Converts a local datetime to UTC."""
    tz = pytz.timezone(config.TIMEZONE)
    if dt.tzinfo is None:
        dt = tz.localize(dt)
    return dt.astimezone(pytz.UTC).replace(tzinfo=None)

def utc_to_local(dt: datetime) -> datetime:
    """Converts a UTC datetime to local timezone."""
    tz = pytz.timezone(config.TIMEZONE)
    dt_utc = pytz.UTC.localize(dt)
    return dt_utc.astimezone(tz)

def format_datetime_for_user(dt: datetime) -> str:
    """Formats a datetime for displaying to the user."""
    local_dt = utc_to_local(dt)
    return local_dt.strftime("%Y-%m-%d at %H:%M")
