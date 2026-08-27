from dotenv import load_dotenv
import os

# Load environment variables from the .env file, if present
load_dotenv()

# Telegram API credentials obtained from https://my.telegram.org/auth
_api_id_raw = os.getenv("API_ID")
API_ID = int(_api_id_raw) if _api_id_raw and _api_id_raw.strip().lstrip("-").isdigit() else 0
API_HASH = os.getenv("API_HASH", "").strip().strip('"').strip("'")

# List of Telegram bot tokens used for file upload/download operations
BOT_TOKENS = [
    token.strip().strip('"').strip("'")
    for token in os.getenv("BOT_TOKENS", "").split(",")
    if token.strip().strip('"').strip("'")
]

# List of Premium Telegram Account Pyrogram String Sessions used for file upload/download operations
STRING_SESSIONS = [
    session.strip().strip('"').strip("'")
    for session in os.getenv("STRING_SESSIONS", "").split(",")
    if session.strip().strip('"').strip("'")
]

# Chat ID or username of the Telegram storage channel where files will be stored
def _parse_storage_channel(raw: str | None):
    if not raw:
        return 0
    raw = str(raw).strip().strip('"').strip("'")
    if not raw:
        return 0
    if "t.me/" in raw:
        path_part = raw.split("t.me/")[-1].strip("/")
        parts = path_part.split("/")
        if parts[0] == "c" and len(parts) >= 2 and parts[1].isdigit():
            return int(f"-100{parts[1]}")
        elif parts[0] and not parts[0].startswith("+"):
            return f"@{parts[0].lstrip('@')}"
    if raw.lstrip("-").isdigit():
        return int(raw)
    if raw.startswith("@") or (raw.replace("_", "").isalnum() and not raw.isdigit()):
        return raw if raw.startswith("@") else f"@{raw}"
    return 0

_storage_channel_raw = os.getenv("STORAGE_CHANNEL")
STORAGE_CHANNEL = _parse_storage_channel(_storage_channel_raw)

# Message ID of a file in the storage channel used for storing database backups
def _parse_db_msg_id(raw: str | None) -> int:
    if not raw:
        return 0
    raw = str(raw).strip().strip('"').strip("'").rstrip("/")
    if "/" in raw:
        raw = raw.split("/")[-1]
    if raw.isdigit():
        return int(raw)
    return 0

_db_msg_id_raw = os.getenv("DATABASE_BACKUP_MSG_ID")
DATABASE_BACKUP_MSG_ID = _parse_db_msg_id(_db_msg_id_raw)

# Password used to access the website's admin panel
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
INSECURE_DEFAULT_PASSWORDS = {"admin", "password", "123456", "secret", "root", "12345678", "admin123"}
IS_DEFAULT_PASSWORD = (not ADMIN_PASSWORD) or (ADMIN_PASSWORD.lower() in INSECURE_DEFAULT_PASSWORDS)

# Email address of the admin. OTP verification codes are sent to this address.
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "").strip()

# SMTP settings for sending OTP emails (compatible with Gmail, Resend, Mailgun, etc.)
# Gmail quick setup: use smtp.gmail.com:587, and create an App Password at
# https://myaccount.google.com/apppasswords (requires 2FA enabled on Gmail)
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "")          # e.g. your Gmail address
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")  # Gmail App Password (16 chars, no spaces)
FROM_EMAIL = os.getenv("FROM_EMAIL", SMTP_USER)  # Sender address shown to recipients
FROM_NAME = os.getenv("FROM_NAME", "TG Drive")   # Sender display name
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "false")  # "true" for port 465 (SMTPS), "false" for STARTTLS

# Determine the maximum file size (in bytes) allowed for uploading to Telegram
# 1.98 GB if no premium sessions are provided, otherwise 3.98 GB
if len(STRING_SESSIONS) == 0:
    MAX_FILE_SIZE = 1.98 * 1024 * 1024 * 1024  # 2 GB in bytes
else:
    MAX_FILE_SIZE = 3.98 * 1024 * 1024 * 1024  # 4 GB in bytes

# Database backup interval in seconds. Backups will be sent to the storage channel at this interval
DATABASE_BACKUP_TIME = int(
    os.getenv("DATABASE_BACKUP_TIME", 60)
)  # Default to 60 seconds

# Time delay in seconds before retrying after a Telegram API floodwait error
SLEEP_THRESHOLD = int(os.getenv("SLEEP_THRESHOLD", 60))  # Default to 60 seconds

# Domain to auto-ping and keep the website active
WEBSITE_URL = os.getenv("WEBSITE_URL", None)


# For Using TG Drive's Bot Mode

# Main Bot Token for TG Drive's Bot Mode
MAIN_BOT_TOKEN = os.getenv("MAIN_BOT_TOKEN", "")
if MAIN_BOT_TOKEN.strip() == "":
    MAIN_BOT_TOKEN = None

# List of Telegram User IDs who have admin access to the bot mode
TELEGRAM_ADMIN_IDS = [
    int(id_str.strip())
    for id_str in os.getenv("TELEGRAM_ADMIN_IDS", "").split(",")
    if id_str.strip().lstrip("-").isdigit()
]


def validate_config(raise_on_error: bool = False) -> tuple[bool, list[str]]:
    """
    Validates essential environment configuration on application startup.
    Returns (is_valid, list_of_diagnostics). Never leaks secret values.
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not API_ID:
        errors.append("API_ID is missing or not a valid integer. Obtain from https://my.telegram.org/auth")
    if not API_HASH or len(API_HASH.strip()) < 8:
        errors.append("API_HASH is missing or invalid.")
    if not BOT_TOKENS:
        errors.append("BOT_TOKENS is empty. At least one bot token is required for storage operations.")
    if not STORAGE_CHANNEL:
        errors.append("STORAGE_CHANNEL is missing. Set to your Telegram storage channel/group ID.")
    if not DATABASE_BACKUP_MSG_ID:
        warnings.append("DATABASE_BACKUP_MSG_ID is 0. Metadata backup to Telegram channel may fail until configured.")
    if IS_DEFAULT_PASSWORD:
        errors.append("ADMIN_PASSWORD is using an insecure default or empty value. Set a strong password.")
    if not ADMIN_EMAIL:
        warnings.append("ADMIN_EMAIL is not set. OTP 2FA verification will not be available.")

    is_valid = len(errors) == 0

    if not is_valid and raise_on_error:
        raise ValueError("Critical configuration errors:\n - " + "\n - ".join(errors))

    return is_valid, errors + warnings
