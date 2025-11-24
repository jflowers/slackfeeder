import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

# Module-level logger
logger = logging.getLogger(__name__)


def setup_logging():
    """Sets up the logging configuration."""
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, log_level, logging.INFO)
    logging.basicConfig(level=level, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    return logging.getLogger(__name__)


def sanitize_folder_name(name: str) -> str:
    """Sanitize folder name for Google Drive.

    Google Drive folder names have restrictions:
    - Maximum 255 characters
    - Cannot contain certain special characters
    - Must not be an absolute path or contain path traversal sequences

    Args:
        name: Folder name to sanitize

    Returns:
        Sanitized folder name safe for Google Drive
    """
    if not name:
        return "unnamed_conversation"

    # Prevent absolute paths - extract basename if absolute path detected
    if os.path.isabs(name):
        name = os.path.basename(name)
        logger.warning(f"Absolute path detected in folder name, using basename: {name}")

    # Remove or replace invalid characters for Google Drive
    # Google Drive doesn't allow: / \ < > : " | ? *
    name = re.sub(r'[/\\<>:"|?*]', "_", name)
    
    # Remove leading/trailing spaces and dots first
    name = name.strip(". ")
    
    # Explicitly prevent path traversal sequences after stripping
    # Replace any remaining ".." sequences (should already be removed by above regex, but be explicit)
    name = name.replace("..", "_")
    
    # Limit length (Google Drive limit is 255 chars)
    if len(name) > 255:
        name = name[:255].rstrip(". ")
    
    # Ensure we have a valid name
    if not name:
        name = "unnamed_conversation"

    return name


def load_json_file(filepath: str) -> Optional[Union[Dict[str, Any], List[Any]]]:
    """Loads a JSON file and returns its content.

    Args:
        filepath: Path to the JSON file

    Returns:
        Parsed JSON content as dict/list, or None if file doesn't exist or is invalid
    """
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        logging.error(f"File not found: {filepath}")
        return None
    except json.JSONDecodeError as e:
        logging.error(f"Error decoding JSON from {filepath}: {e}")
        return None
    except IOError as e:
        logging.error(f"IO error reading file {filepath}: {e}")
        return None


def validate_channels_json(data: Any) -> bool:
    """Validate channels.json structure.

    Args:
        data: Parsed JSON data

    Returns:
        True if valid, raises ValueError if invalid
    """
    if not isinstance(data, dict):
        raise ValueError("channels.json must be a JSON object")
    if "channels" not in data:
        raise ValueError("channels.json must contain 'channels' key")
    if not isinstance(data["channels"], list):
        raise ValueError("'channels' must be a list")
    return True


def validate_people_json(data: Any) -> bool:
    """Validate people.json structure.

    Args:
        data: Parsed JSON data

    Returns:
        True if valid, raises ValueError if invalid
    """
    if not isinstance(data, dict):
        raise ValueError("people.json must be a JSON object")
    if "people" not in data:
        raise ValueError("people.json must contain 'people' key")
    if not isinstance(data["people"], list):
        raise ValueError("'people' must be a list")
    for person in data["people"]:
        if not isinstance(person, dict):
            raise ValueError("Each person must be a dictionary")
        if "slackId" not in person:
            raise ValueError("Each person must have 'slackId'")
    return True


def validate_channel_id(channel_id: str) -> bool:
    """Validate Slack channel ID format.

    Args:
        channel_id: Channel ID to validate

    Returns:
        True if valid format, False otherwise
    """
    if not channel_id or not isinstance(channel_id, str):
        return False
    # Slack IDs are typically 9-11 characters, starting with C, D, or G
    pattern = r"^[CDG][A-Z0-9]{8,10}$"
    return bool(re.match(pattern, channel_id))


def save_json_file(data: Any, filepath: str) -> bool:
    """Saves data to a JSON file.

    Args:
        data: Data to save (dict, list, etc.)
        filepath: Path where to save the file

    Returns:
        True if successful, False otherwise
    """
    try:
        # Ensure directory exists
        dir_path = os.path.dirname(filepath)
        if dir_path and not os.path.exists(dir_path):
            os.makedirs(dir_path, exist_ok=True)

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
            f.flush()
            os.fsync(f.fileno())  # Ensure data is written to disk

        # Verify file was written successfully
        if not os.path.exists(filepath):
            logging.error(f"File write verification failed for {filepath}")
            return False

        logging.info(f"Successfully saved data to {filepath}")
        return True
    except IOError as e:
        logging.error(f"Failed to write to file {filepath}: {e}")
        return False
    except Exception as e:
        logging.error(f"Unexpected error saving file {filepath}: {e}")
        return False


def convert_date_to_timestamp(date_str: Optional[str], is_end_date: bool = False) -> Optional[str]:
    """Converts YYYY-MM-DD or YYYY-MM-DD HH:MM:SS string (assumed UTC) to Unix timestamp string.

    Args:
        date_str: Date string in format 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS'
        is_end_date: If True, sets time to end of day for date-only format

    Returns:
        Unix timestamp as string, or None if date_str is invalid/empty
    """
    if not date_str:
        return None

    # Strip whitespace
    date_str = date_str.strip()
    if not date_str:
        return None

    try:
        # Try full datetime format
        dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        try:
            # Try just date format
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            if is_end_date:
                # If it's an end date, set time to the very end of that day
                dt = dt.replace(hour=23, minute=59, second=59)
        except ValueError:
            logging.error(
                f"Invalid date format: {date_str}. Use 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS'."
            )
            return None  # Return None instead of "ERROR"

    # Assume the provided time is in UTC and get the timestamp
    return str(dt.replace(tzinfo=timezone.utc).timestamp())


def create_directory(dir_path: str) -> bool:
    """Creates a directory if it doesn't exist.

    Args:
        dir_path: Path to directory to create

    Returns:
        True if directory exists or was created successfully, False otherwise
    """
    if not os.path.exists(dir_path):
        try:
            os.makedirs(dir_path)
            logging.info(f"Created directory: {dir_path}")
        except OSError as e:
            logging.error(f"Failed to create directory {dir_path}: {e}")
            return False
    return True


# Constants
MAX_FILENAME_LENGTH = 200  # Maximum filename length


def sanitize_filename(filename: str) -> str:
    """Remove path separators and dangerous characters from filename.

    Args:
        filename: The filename to sanitize

    Returns:
        A sanitized filename safe for use in file paths
    """
    if not filename:
        return "unnamed"

    # Remove path separators and parent directory references
    filename = filename.replace("/", "_").replace("\\", "_")
    filename = filename.replace("..", "_")
    # Remove any remaining dangerous characters
    filename = re.sub(r'[<>:"|?*]', "_", filename)
    # Remove leading/trailing dots and spaces
    filename = filename.strip(". ")
    # Limit length
    filename = filename[:MAX_FILENAME_LENGTH]
    # Ensure we have a valid filename
    if not filename:
        filename = "unnamed"

    return filename


def format_timestamp(timestamp_str: Optional[str]) -> Optional[str]:
    """Converts a Unix timestamp string to a readable datetime string.

    Args:
        timestamp_str: Unix timestamp as string

    Returns:
        Formatted datetime string, or original string if conversion fails, or None if input is None
    """
    if timestamp_str is None:
        return None
    try:
        ts = float(timestamp_str)
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except (ValueError, TypeError):
        return timestamp_str


def sanitize_path_for_logging(filepath: str) -> str:
    """Sanitize file paths for logging to avoid exposing sensitive directory structures.

    Args:
        filepath: Full file path to sanitize

    Returns:
        Sanitized path showing only filename or last component
    """
    if not filepath:
        return "[empty path]"
    # Return only the filename, not the full path
    return os.path.basename(filepath)


def validate_email(email: str) -> bool:
    """Validate email format.

    Args:
        email: Email address to validate

    Returns:
        True if valid format, False otherwise
    """
    if not email or not isinstance(email, str):
        return False

    email = email.strip()

    # Basic length checks
    if len(email) < 3 or len(email) > 254:  # RFC 5321 limits
        return False

    # Check for @ symbol
    if "@" not in email:
        return False

    # Split into local and domain parts
    parts = email.split("@")
    if len(parts) != 2:
        return False

    local_part, domain_part = parts

    # Validate local part (before @)
    if not local_part or len(local_part) > 64:  # RFC 5321 limit
        return False
    # Cannot start or end with dot
    if local_part.startswith(".") or local_part.endswith("."):
        return False
    # Cannot have consecutive dots
    if ".." in local_part:
        return False

    # Validate domain part (after @)
    if not domain_part or len(domain_part) > 253:  # RFC 5321 limit
        return False

    # Check for TLD (at least one dot required)
    if "." not in domain_part:
        return False

    # Domain cannot start or end with dot or hyphen
    if (
        domain_part.startswith(".")
        or domain_part.endswith(".")
        or domain_part.startswith("-")
        or domain_part.endswith("-")
    ):
        return False

    # More comprehensive regex pattern
    # Allows letters, numbers, dots, hyphens, underscores, plus signs, and percent signs in local part
    # Domain must have valid TLD (2+ letters)
    pattern = r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?)*\.[a-zA-Z]{2,}$"

    return bool(re.match(pattern, email))
