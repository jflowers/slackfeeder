#!/usr/bin/env python3
"""Bulk rename files in Google Drive folders to follow naming convention.

Files should be prefixed with the folder name. Files that don't follow this
convention will be renamed. If renaming would create a duplicate (file with
correct name already exists), the incorrectly named file will be moved to trash.
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

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

logger = logging.getLogger(__name__)


def extract_person_name(folder_name: str) -> str:
    """Extract the person's name (before comma) from folder name.
    
    Args:
        folder_name: Full folder name like "Tara, Jay Flowers"
        
    Returns:
        Person's name like "Tara" or "Beatriz Couto"
    """
    # Split by comma and take first part
    parts = folder_name.split(",")
    if len(parts) > 1:
        return parts[0].strip()
    return folder_name.strip()


def check_file_naming_convention(folder_name: str, file_name: str) -> bool:
    """Check if file name follows the naming convention (folder name as prefix).

    Args:
        folder_name: Name of the folder
        file_name: Name of the file

    Returns:
        True if file follows convention, False otherwise
    """
    # Sanitize folder name for comparison (same as used in Google Drive)
    sanitized_folder = sanitize_folder_name(folder_name)
    
    # File should start with folder name followed by space (for "slack messages" pattern)
    # Pattern: "{folder_name} slack messages {date}"
    if file_name.startswith(sanitized_folder + " "):
        return True
    
    # Check for incorrect pattern: "{folder_name}_{person_name} slack messages..."
    # This should NOT be considered valid (it has duplicate person name)
    person_name = extract_person_name(folder_name)
    sanitized_person = sanitize_folder_name(person_name)
    incorrect_pattern = sanitized_folder + "_" + sanitized_person + " "
    if file_name.startswith(incorrect_pattern):
        return False  # This is the incorrect pattern we need to fix
    
    # Also check underscore pattern for backward compatibility (but not the duplicate pattern)
    if file_name.startswith(sanitized_folder + "_"):
        # Only accept if it's NOT followed by person name
        if not file_name.startswith(sanitized_folder + "_" + sanitized_person + " "):
            return True
    
    return False


def generate_correct_filename(folder_name: str, file_name: str) -> str:
    """Generate the correct filename with folder name prefix.

    Args:
        folder_name: Name of the folder (e.g., "Tara, Jay Flowers")
        file_name: Current name of the file (e.g., "Tara slack messages 20251121")

    Returns:
        Correct filename (e.g., "Tara, Jay Flowers slack messages 20251121")
    """
    sanitized_folder = sanitize_folder_name(folder_name)
    
    # Extract person's name (before comma) from folder name
    person_name = extract_person_name(folder_name)
    sanitized_person = sanitize_folder_name(person_name)
    
    # FIRST: Check for incorrect pattern: "{folder_name}_{person_name} slack messages..."
    # Example: "Tara, Jay Flowers_Tara slack messages 20251120"
    # Should become: "Tara, Jay Flowers slack messages 20251120"
    incorrect_pattern = sanitized_folder + "_" + sanitized_person + " "
    if file_name.startswith(incorrect_pattern):
        # Remove the duplicate person name part
        suffix = file_name[len(incorrect_pattern):]
        return sanitized_folder + " " + suffix
    
    # If file already starts with full folder name followed by space, keep it
    if file_name.startswith(sanitized_folder + " "):
        return file_name
    
    # Check if file starts with just the person's name (without "Jay Flowers")
    # Pattern: "{person_name} slack messages {date}"
    if file_name.startswith(sanitized_person + " "):
        # Replace person's name with full folder name
        suffix = file_name[len(sanitized_person):]
        return sanitized_folder + suffix
    
    # Check if file starts with person's name + underscore
    if file_name.startswith(sanitized_person + "_"):
        # Replace person's name with full folder name, but use space instead of underscore
        suffix = file_name[len(sanitized_person + "_"):]
        return sanitized_folder + " " + suffix
    
    # If file starts with folder name + underscore (but not the duplicate pattern), convert to space
    if file_name.startswith(sanitized_folder + "_"):
        suffix = file_name[len(sanitized_folder + "_"):]
        return sanitized_folder + " " + suffix
    
    # If file doesn't start with person's name or folder name, prepend folder name with space
    return sanitized_folder + " " + file_name


def process_conversation(
    drive_client: GoogleDriveClient,
    conversation_name: str,
    dry_run: bool = False
) -> dict:
    """Process files in a conversation folder.

    Args:
        drive_client: GoogleDriveClient instance
        conversation_name: Name of the conversation (folder name)
        dry_run: If True, only report what would be done without making changes

    Returns:
        Dictionary with statistics about the operation
    """
    stats = {
        "conversation": conversation_name,
        "folder_found": False,
        "files_checked": 0,
        "files_renamed": 0,
        "files_trashed": 0,
        "files_skipped": 0,
        "errors": []
    }
    
    # Find the folder
    sanitized_folder_name = sanitize_folder_name(conversation_name)
    folder_id = drive_client.find_folder(sanitized_folder_name)
    
    if not folder_id:
        logger.warning(f"Folder '{conversation_name}' not found in Google Drive")
        stats["errors"].append(f"Folder not found: {conversation_name}")
        return stats
    
    stats["folder_found"] = True
    logger.info(f"Found folder '{conversation_name}' (ID: {folder_id})")
    
    # List all files in the folder
    files = drive_client.list_files_in_folder(folder_id)
    stats["files_checked"] = len(files)
    logger.info(f"Found {len(files)} files in folder")
    
    if not files:
        return stats
    
    # Check each file
    for file_info in files:
        file_id = file_info.get("id")
        file_name = file_info.get("name", "")
        
        # Skip metadata files
        if file_name.endswith("_last_export.json"):
            logger.debug(f"Skipping metadata file: {file_name}")
            stats["files_skipped"] += 1
            continue
        
        # Check if file follows naming convention
        if check_file_naming_convention(conversation_name, file_name):
            logger.debug(f"File '{file_name}' already follows naming convention")
            stats["files_skipped"] += 1
            continue
        
        # Generate correct filename
        correct_filename = generate_correct_filename(conversation_name, file_name)
        
        # Check if a file with the correct name already exists
        escaped_correct_name = drive_client._escape_drive_query_string(correct_filename)
        escaped_folder_id = drive_client._escape_drive_query_string(folder_id)
        query = (
            f"name='{escaped_correct_name}' and '{escaped_folder_id}' in parents "
            f"and trashed=false"
        )
        
        try:
            drive_client._rate_limit()
            results = (
                drive_client.service.files()
                .list(q=query, fields="files(id, name)", pageSize=1)
                .execute()
            )
            existing_files = results.get("files", [])
            
            if existing_files:
                # File with correct name already exists, move incorrect one to trash
                logger.warning(
                    f"File '{file_name}' would conflict with existing '{correct_filename}', "
                    f"moving to trash"
                )
                if not dry_run:
                    if drive_client.trash_file(file_id):
                        stats["files_trashed"] += 1
                    else:
                        stats["errors"].append(f"Failed to trash file: {file_name}")
                else:
                    stats["files_trashed"] += 1
            else:
                # No conflict, rename the file
                logger.info(f"Renaming '{file_name}' to '{correct_filename}'")
                if not dry_run:
                    if drive_client.rename_file(file_id, correct_filename):
                        stats["files_renamed"] += 1
                    else:
                        stats["errors"].append(f"Failed to rename file: {file_name}")
                else:
                    stats["files_renamed"] += 1
        except Exception as e:
            error_msg = f"Error processing file '{file_name}': {e}"
            logger.error(error_msg, exc_info=True)
            stats["errors"].append(error_msg)
    
    return stats


def main():
    """Main function."""
    parser = argparse.ArgumentParser(
        description="Rename files in Google Drive folders to follow naming convention"
    )
    parser.add_argument(
        "--browser-export-config",
        type=str,
        default="config/browser-export.json",
        help="Path to browser-export.json file",
    )
    parser.add_argument(
        "--credentials",
        type=str,
        help="Path to Google Drive credentials file (overrides GOOGLE_DRIVE_CREDENTIALS_FILE)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )
    parser.add_argument(
        "--conversation",
        type=str,
        help="Process only a specific conversation (by name)",
    )
    
    args = parser.parse_args()
    
    # Setup logging
    setup_logging()
    
    # Get credentials file
    credentials_file = args.credentials
    if not credentials_file:
        credentials_file = os.getenv("GOOGLE_DRIVE_CREDENTIALS_FILE", "").strip()
    
    if not credentials_file:
        logger.error("GOOGLE_DRIVE_CREDENTIALS_FILE environment variable or --credentials required")
        sys.exit(1)
    
    credentials_file = os.path.abspath(os.path.expanduser(credentials_file))
    if not os.path.exists(credentials_file):
        logger.error(f"Credentials file not found: {credentials_file}")
        sys.exit(1)
    
    # Load browser-export.json
    browser_export_path = os.path.abspath(os.path.expanduser(args.browser_export_config))
    browser_export_data = load_json_file(browser_export_path)
    
    if not browser_export_data:
        logger.error(f"Failed to load {browser_export_path}")
        sys.exit(1)
    
    if "browser-export" not in browser_export_data:
        logger.error(f"Invalid browser-export.json: missing 'browser-export' key")
        sys.exit(1)
    
    conversations = browser_export_data["browser-export"]
    if not isinstance(conversations, list):
        logger.error(f"Invalid browser-export.json: 'browser-export' must be a list")
        sys.exit(1)
    
    # Filter conversations if specific one requested
    if args.conversation:
        conversations = [
            conv for conv in conversations
            if conv.get("name", "") == args.conversation
        ]
        if not conversations:
            logger.error(f"Conversation '{args.conversation}' not found in browser-export.json")
            sys.exit(1)
    
    # Initialize Google Drive client
    try:
        drive_client = GoogleDriveClient(credentials_file)
    except Exception as e:
        logger.error(f"Failed to initialize Google Drive client: {e}", exc_info=True)
        sys.exit(1)
    
    # Process each conversation
    all_stats = []
    for conv in conversations:
        conv_name = conv.get("name", "")
        if not conv_name:
            logger.warning("Skipping conversation with no name")
            continue
        
        logger.info(f"\n{'='*60}")
        logger.info(f"Processing conversation: {conv_name}")
        logger.info(f"{'='*60}")
        
        stats = process_conversation(drive_client, conv_name, dry_run=args.dry_run)
        all_stats.append(stats)
        
        # Print summary
        logger.info(f"\nSummary for {conv_name}:")
        logger.info(f"  Files checked: {stats['files_checked']}")
        logger.info(f"  Files renamed: {stats['files_renamed']}")
        logger.info(f"  Files trashed: {stats['files_trashed']}")
        logger.info(f"  Files skipped: {stats['files_skipped']}")
        if stats["errors"]:
            logger.warning(f"  Errors: {len(stats['errors'])}")
            for error in stats["errors"]:
                logger.warning(f"    - {error}")
    
    # Print overall summary
    logger.info(f"\n{'='*60}")
    logger.info("Overall Summary")
    logger.info(f"{'='*60}")
    total_checked = sum(s["files_checked"] for s in all_stats)
    total_renamed = sum(s["files_renamed"] for s in all_stats)
    total_trashed = sum(s["files_trashed"] for s in all_stats)
    total_skipped = sum(s["files_skipped"] for s in all_stats)
    total_errors = sum(len(s["errors"]) for s in all_stats)
    
    logger.info(f"Conversations processed: {len(all_stats)}")
    logger.info(f"Total files checked: {total_checked}")
    logger.info(f"Total files renamed: {total_renamed}")
    logger.info(f"Total files trashed: {total_trashed}")
    logger.info(f"Total files skipped: {total_skipped}")
    if total_errors > 0:
        logger.warning(f"Total errors: {total_errors}")
    
    if args.dry_run:
        logger.info("\nDRY RUN MODE - No changes were made")


if __name__ == "__main__":
    main()
