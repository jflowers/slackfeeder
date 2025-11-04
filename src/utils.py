import json
import logging
import os
from datetime import datetime, timezone

def setup_logging():
    """Sets up the logging configuration."""
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    return logging.getLogger(__name__)

def load_json_file(filepath):
    """Loads a JSON file and returns its content."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        logging.error(f"File not found: {filepath}")
        return None
    except json.JSONDecodeError as e:
        logging.error(f"Error decoding JSON from {filepath}: {e}")
        return None

def save_json_file(data, filepath):
    """Saves data to a JSON file."""
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=4)
        logging.info(f"Successfully saved data to {filepath}")
    except IOError as e:
        logging.error(f"Failed to write to file {filepath}: {e}")

def convert_date_to_timestamp(date_str, is_end_date=False):
    """Converts YYYY-MM-DD or YYYY-MM-DD HH:MM:SS string (assumed UTC) to Unix timestamp string."""
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
            return "ERROR"  # Use a sentinel to stop execution

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
    """Converts a Unix timestamp string to a readable datetime string."""
    try:
        ts = float(timestamp_str)
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except (ValueError, TypeError):
        return timestamp_str
