import json
import logging
import os
import re
from datetime import datetime, timezone
from typing import Optional

def setup_logging():
    """Sets up the logging configuration."""
    log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
    level = getattr(logging, log_level, logging.INFO)
    logging.basicConfig(
        level=level,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )
    return logging.getLogger(__name__)

def sanitize_filename(filename):
    """Remove path separators and dangerous characters from filename.
    
    Args:
        filename: The filename to sanitize
        
    Returns:
        A sanitized filename safe for use in file paths
    """
    if not filename:
        return "unnamed"
    
    # Remove path separators and parent directory references
    filename = filename.replace('/', '_').replace('\\', '_')
    filename = filename.replace('..', '_')
    # Remove any remaining dangerous characters
    filename = re.sub(r'[<>:"|?*]', '_', filename)
    # Remove leading/trailing dots and spaces
    filename = filename.strip('. ')
    # Limit length
    filename = filename[:200]
    # Ensure we have a valid filename
    if not filename:
        filename = "unnamed"
    
    return filename

def load_json_file(filepath: str):
    """Loads a JSON file and returns its content.
    
    Args:
        filepath: Path to the JSON file
        
    Returns:
        Parsed JSON content as dict/list, or None if file doesn't exist or is invalid
    """
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
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

def validate_channels_json(data) -> bool:
    """Validate channels.json structure.
    
    Args:
        data: Parsed JSON data
        
    Returns:
        True if valid, raises ValueError if invalid
    """
    if not isinstance(data, dict):
        raise ValueError("channels.json must be a JSON object")
    if 'channels' not in data:
        raise ValueError("channels.json must contain 'channels' key")
    if not isinstance(data['channels'], list):
        raise ValueError("'channels' must be a list")
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
    pattern = r'^[CDG][A-Z0-9]{8,10}$'
    return bool(re.match(pattern, channel_id))

def save_json_file(data, filepath):
    """Saves data to a JSON file."""
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        logging.info(f"Successfully saved data to {filepath}")
    except IOError as e:
        logging.error(f"Failed to write to file {filepath}: {e}")

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
            logging.error(f"Invalid date format: {date_str}. Use 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS'.")
            return None  # Return None instead of "ERROR"

    # Assume the provided time is in UTC and get the timestamp
    return str(dt.replace(tzinfo=timezone.utc).timestamp())

def create_directory(dir_path):
    """Creates a directory if it doesn't exist."""
    if not os.path.exists(dir_path):
        try:
            os.makedirs(dir_path)
            logging.info(f"Created directory: {dir_path}")
        except OSError as e:
            logging.error(f"Failed to create directory {dir_path}: {e}")
            return False
    return True

def format_timestamp(timestamp_str):
    """Converts a Unix timestamp string to a readable datetime string.
    
    Args:
        timestamp_str: Unix timestamp as string
        
    Returns:
        Formatted datetime string, or original string if conversion fails
    """
    try:
        ts = float(timestamp_str)
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except (ValueError, TypeError):
        return timestamp_str
