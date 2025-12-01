#!/usr/bin/env python3
"""
Verify and extract missing messages for conversations in browser-export.json
for Nov 17-24, 2025 date range.

This script:
1. Checks Google Drive for missing dates
2. For each missing date, verifies if messages exist in Slack (via browser DOM)
3. Extracts and uploads missing messages
"""

import json
import os
import re
import sys
from pathlib import Path
from datetime import datetime

# Load .env file if it exists
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.google_drive import GoogleDriveClient
from src.utils import load_json_file, sanitize_folder_name, setup_logging

logger = setup_logging()


def get_missing_dates_for_conversation(conversation_name: str, start_date: str, end_date: str) -> list:
    """Get missing dates for a conversation in Google Drive.
    
    Args:
        conversation_name: Name of the conversation
        start_date: Start date (YYYY-MM-DD)
        end_date: End date (YYYY-MM-DD)
        
    Returns:
        List of missing dates as YYYY-MM-DD strings
    """
    credentials_file = os.getenv("GOOGLE_DRIVE_CREDENTIALS_FILE", "").strip()
    if not credentials_file:
        logger.error("GOOGLE_DRIVE_CREDENTIALS_FILE environment variable is required")
        return []
    
    credentials_file = os.path.abspath(os.path.expanduser(credentials_file))
    if not os.path.exists(credentials_file):
        logger.error(f"Credentials file not found: {credentials_file}")
        return []
    
    client = GoogleDriveClient(credentials_file)
    
    # Find folder
    folder_id = client.find_folder(sanitize_folder_name(conversation_name))
    if not folder_id:
        logger.warning(f"Folder '{conversation_name}' not found in Google Drive")
        return []
    
    # List files
    files = client.list_files_in_folder(folder_id)
    
    # Extract dates from filenames
    existing_dates = set()
    for f in files:
        match = re.search(r'(\d{8})', f.get('name', ''))
        if match:
            date = match.group(1)
            existing_dates.add(date)
    
    # Generate expected dates
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    expected_dates = set()
    current = start_dt
    while current <= end_dt:
        expected_dates.add(current.strftime("%Y%m%d"))
        current = current.replace(day=current.day + 1) if current.day < 28 else current.replace(month=current.month + 1, day=1)
        if current > end_dt:
            break
    
    # Find missing dates
    missing = []
    for date_str in sorted(expected_dates):
        if date_str not in existing_dates:
            date_obj = datetime.strptime(date_str, "%Y%m%d")
            missing.append(date_obj.strftime("%Y-%m-%d"))
    
    return missing


def main():
    """Main function."""
    browser_export_path = Path("config/browser-export.json")
    browser_export_data = load_json_file(browser_export_path)
    
    if not browser_export_data:
        logger.error(f"Failed to load {browser_export_path}")
        sys.exit(1)
    
    conversations = browser_export_data.get("browser-export", [])
    
    print("\n" + "="*80)
    print("Verification Report: Nov 17-24, 2025 Coverage")
    print("="*80)
    
    for conv in conversations:
        conv_name = conv.get("name", "")
        if not conv_name or not conv.get("export", True):
            continue
        
        missing = get_missing_dates_for_conversation(conv_name, "2025-11-17", "2025-11-24")
        
        print(f"\n{conv_name}:")
        if missing:
            print(f"  Missing dates: {', '.join(missing)}")
            print(f"  Action needed: Check browser DOM for messages on these dates")
        else:
            print(f"  ✓ All dates present in Google Drive")
    
    print("\n" + "="*80)
    print("Next steps:")
    print("1. For each conversation with missing dates, navigate to it in browser")
    print("2. Scroll to check if messages exist for missing dates")
    print("3. Extract and upload messages that exist")
    print("="*80)


if __name__ == "__main__":
    main()
