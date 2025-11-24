#!/usr/bin/env python3
"""
Check for missing dates in Google Drive exports for a conversation.
"""

import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from dotenv import load_dotenv
from src.google_drive import GoogleDriveClient
from src.utils import setup_logging

load_dotenv()

logger = setup_logging()


def extract_date_from_filename(filename: str) -> str:
    """Extract date (YYYYMMDD) from Google Doc filename.
    
    Expected format: "Beatriz Couto slack messages 20251030"
    """
    # Look for 8-digit date pattern (YYYYMMDD)
    match = re.search(r'(\d{8})', filename)
    if match:
        return match.group(1)
    return None


def get_all_dates_in_range(start_date: str, end_date: str) -> set:
    """Generate all dates in range as YYYYMMDD strings."""
    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")
    dates = set()
    current = start
    while current <= end:
        dates.add(current.strftime("%Y%m%d"))
        current += timedelta(days=1)
    return dates


def find_missing_dates(conversation_name: str, start_date: str, end_date: str) -> list:
    """Find missing dates in Google Drive for a conversation.
    
    Args:
        conversation_name: Name of the conversation (folder name)
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
        
    Returns:
        List of missing dates as YYYY-MM-DD strings
    """
    credentials_file = os.getenv("GOOGLE_DRIVE_CREDENTIALS_FILE", "").strip()
    if not credentials_file:
        logger.error("GOOGLE_DRIVE_CREDENTIALS_FILE environment variable is required")
        sys.exit(1)
    
    credentials_file = os.path.abspath(os.path.expanduser(credentials_file))
    if not os.path.exists(credentials_file):
        logger.error(f"Credentials file not found: {credentials_file}")
        sys.exit(1)
    
    drive_client = GoogleDriveClient(credentials_file)
    
    # Find the folder
    folder_id = drive_client.find_folder(conversation_name)
    if not folder_id:
        logger.error(f"Folder '{conversation_name}' not found in Google Drive")
        sys.exit(1)
    
    logger.info(f"Found folder '{conversation_name}' with ID: {folder_id}")
    
    # List all files in the folder
    files = drive_client.list_files_in_folder(folder_id)
    logger.info(f"Found {len(files)} files in folder")
    
    # Extract dates from filenames
    existing_dates = set()
    for file_info in files:
        filename = file_info.get("name", "")
        date_str = extract_date_from_filename(filename)
        if date_str:
            existing_dates.add(date_str)
            logger.debug(f"Found date {date_str} in file: {filename}")
    
    # Get all expected dates
    expected_dates = get_all_dates_in_range(start_date, end_date)
    
    # Find missing dates
    missing_dates = []
    for date_str in sorted(expected_dates):
        if date_str not in existing_dates:
            # Convert YYYYMMDD to YYYY-MM-DD
            date_obj = datetime.strptime(date_str, "%Y%m%d")
            missing_dates.append(date_obj.strftime("%Y-%m-%d"))
    
    return missing_dates


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Check for missing dates in Google Drive exports")
    parser.add_argument("conversation_name", help="Conversation name (folder name in Google Drive)")
    parser.add_argument("--start-date", required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", required=True, help="End date (YYYY-MM-DD)")
    
    args = parser.parse_args()
    
    missing_dates = find_missing_dates(args.conversation_name, args.start_date, args.end_date)
    
    if missing_dates:
        print(f"\nFound {len(missing_dates)} missing dates:")
        for date in missing_dates:
            print(f"  - {date}")
    else:
        print(f"\nNo missing dates found! All dates from {args.start_date} to {args.end_date} are present.")
    
    sys.exit(0 if not missing_dates else 1)
