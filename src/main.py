import argparse
import os
import sys
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from src.utils import (
    setup_logging,
    load_json_file,
    save_json_file,
    convert_date_to_timestamp,
    create_directory,
    format_timestamp,
    sanitize_filename,
    sanitize_folder_name,
    validate_channels_json,
    validate_channel_id,
    validate_email,
    validate_people_json,
)
from src.slack_client import SlackClient, SHARE_RATE_LIMIT_INTERVAL, SHARE_RATE_LIMIT_DELAY
from src.google_drive import GoogleDriveClient
from slack_sdk.errors import SlackApiError

logger = setup_logging()

# Constants
CONVERSATION_DELAY_SECONDS = 0.5
LARGE_CONVERSATION_THRESHOLD = 10000
MAX_FILE_SIZE_MB = int(os.getenv('MAX_EXPORT_FILE_SIZE_MB', '100'))
MAX_MESSAGES_PER_CONVERSATION = int(os.getenv('MAX_MESSAGES_PER_CONVERSATION', '50000'))
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024
# Maximum date range in days (1 year)
MAX_DATE_RANGE_DAYS = int(os.getenv('MAX_DATE_RANGE_DAYS', '365'))

def preprocess_history(history_data: List[Dict[str, Any]], slack_client: SlackClient, people_cache: Optional[Dict[str, str]] = None) -> str:
    """Processes Slack history into a human-readable format."""
    threads = {}
    for message in history_data:
        text = message.get('text', '')
        files = message.get('files')

        # If no text and no files, skip
        if not text and not files:
            continue
        
        # If no text but has files, use a placeholder
        if not text and files:
            text = "[File attached]"
        # If text and files, append placeholder
        elif text and files:
            text += " [File attached]"

        thread_key = message.get('thread_ts', message.get('ts'))
        if not thread_key:
            continue

        if thread_key not in threads:
            threads[thread_key] = []
        
        ts = message.get('ts')
        
        user_id = message.get('user')
        name = "Unknown User"
        if user_id:
            # Check cache first
            if people_cache and user_id in people_cache:
                name = people_cache[user_id]
            else:
                user_info = slack_client.get_user_info(user_id)
                if user_info:
                    name = user_info.get("displayName", message.get('username', user_id))
                    # Update cache for future use
                    if people_cache is not None:
                        people_cache[user_id] = name
        
        text = text.replace('\n', '\n    ')
        
        threads[thread_key].append((ts, name, text))

    sorted_thread_keys = sorted(threads.keys())
    output_lines = []
    for thread_key in sorted_thread_keys:
        messages_in_thread = sorted(threads[thread_key], key=lambda m: m[0])
        
        parent_ts, parent_name, parent_text = messages_in_thread[0]
        formatted_time = format_timestamp(parent_ts)
        if formatted_time is None:
            formatted_time = str(parent_ts) if parent_ts else "[Invalid timestamp]"
        output_lines.append(f"[{formatted_time}] {parent_name}: {parent_text}")
        
        for (reply_ts, reply_name, reply_text) in messages_in_thread[1:]:
            formatted_reply_time = format_timestamp(reply_ts)
            if formatted_reply_time is None:
                formatted_reply_time = str(reply_ts) if reply_ts else "[Invalid timestamp]"
            output_lines.append(f"    > [{formatted_reply_time}] {reply_name}: {reply_text}")
        
        output_lines.append("\n")
    
    return "\n".join(output_lines)

def get_conversation_display_name(channel_info: Dict[str, Any], slack_client: SlackClient) -> str:
    """Gets the display name for a conversation, handling channels, DMs, and group chats.
    
    Args:
        channel_info: Dictionary containing channel information
        slack_client: SlackClient instance for API calls
        
    Returns:
        Display name for the conversation, never None or empty
    """
    display_name = channel_info.get("displayName")
    if display_name:
        return display_name
    
    channel_id = channel_info.get("id")
    if not channel_id:
        logger.warning("Channel info missing ID")
        return "unknown_conversation"
    
    # For group DMs, create a name from participants
    if channel_info.get("is_mpim"):
        members = channel_info.get("members", [])
        if not members:
            logger.warning(f"Group DM {channel_id} has no members")
            return f"group_dm_{channel_id[:8]}"
        names = []
        for member_id in members:
            user_info = slack_client.get_user_info(member_id)
            if user_info:
                names.append(user_info.get("displayName", member_id))
        if names:
            return ", ".join(sorted(names))
        else:
            return f"group_dm_{channel_id[:8]}"
    
    # For DMs, get the other user's name
    if channel_info.get("is_im"):
        other_user_id = channel_info.get("user")
        if other_user_id:
            user_info = slack_client.get_user_info(other_user_id)
            if user_info:
                return user_info.get("displayName", other_user_id)
        return f"dm_{channel_id[:8]}"
    
    # For channels, use name or fallback to ID
    name = channel_info.get("name") or channel_id
    return name if name else f"conversation_{channel_id[:8]}"

def main(args):
    """Main function to run the Slack history export and upload process."""
    # Get configuration from environment variables with validation
    slack_bot_token = os.getenv("SLACK_BOT_TOKEN", "").strip()
    google_drive_credentials_file = os.getenv("GOOGLE_DRIVE_CREDENTIALS_FILE", "").strip()
    google_drive_folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "").strip()
    
    if not slack_bot_token:
        logger.error("SLACK_BOT_TOKEN environment variable is required and cannot be empty. Exiting.")
        sys.exit(1)
    
    if not google_drive_credentials_file:
        logger.error("GOOGLE_DRIVE_CREDENTIALS_FILE environment variable is required and cannot be empty. Exiting.")
        sys.exit(1)
    
    # Validate and sanitize credentials file path
    try:
        # Resolve to absolute path to prevent traversal
        google_drive_credentials_file = os.path.abspath(os.path.expanduser(google_drive_credentials_file))
        if not os.path.exists(google_drive_credentials_file):
            logger.error(f"Credentials file not found: {google_drive_credentials_file}")
            sys.exit(1)
        if not os.path.isfile(google_drive_credentials_file):
            logger.error(f"Credentials path is not a file: {google_drive_credentials_file}")
            sys.exit(1)
        # Check if file is readable
        if not os.access(google_drive_credentials_file, os.R_OK):
            logger.error(f"Credentials file is not readable: {google_drive_credentials_file}")
            sys.exit(1)
    except (OSError, ValueError) as e:
        logger.error(f"Invalid credentials file path: {e}")
        sys.exit(1)
    
    if not google_drive_folder_id:
        logger.warning("GOOGLE_DRIVE_FOLDER_ID not set. Files will be uploaded to Drive root.")
    
    slack_client = SlackClient(slack_bot_token)
    google_drive_client = GoogleDriveClient(google_drive_credentials_file)

    if args.make_ref_files:
        logger.info("Fetching all conversations and users to create reference files...")
        channels = slack_client.get_all_channels()
        
        # Add export flag (defaults to true) to each conversation
        # Preserve existing export flags if channels.json already exists
        existing_channels_data = load_json_file("config/channels.json")
        existing_export_map = {}
        if existing_channels_data:
            for ch in existing_channels_data.get("channels", []):
                if "id" in ch:
                    existing_export_map[ch["id"]] = ch.get("export", True)
        
        channels_with_export = []
        for channel in channels:
            channel_entry = dict(channel)
            # Preserve existing export setting, or default to True
            if channel_entry.get("id") in existing_export_map:
                channel_entry["export"] = existing_export_map[channel_entry.get("id")]
            elif "export" not in channel_entry:
                channel_entry["export"] = True
            channels_with_export.append(channel_entry)
        
        people = {}
        for channel in channels:
            members = slack_client.get_channel_members(channel["id"])
            for member_id in members:
                if member_id not in people:
                    user_info = slack_client.get_user_info(member_id)
                    if user_info:
                        people[member_id] = user_info
        
        save_json_file({"channels": channels_with_export}, "config/channels.json")
        save_json_file({"people": list(people.values())}, "config/people.json")
        logger.info("Reference files created successfully.")
        logger.info(f"Found {len(channels_with_export)} conversations. Set 'export: false' in channels.json to exclude any you don't want to export.")

    if args.export_history:
        channels_data = load_json_file("config/channels.json")
        if not channels_data:
            logger.error("Could not load channels from config/channels.json. Exiting.")
            logger.info("Run with --make-ref-files first to generate channels.json")
            return

        # Validate JSON structure
        try:
            validate_channels_json(channels_data)
        except ValueError as e:
            logger.error(f"Invalid channels.json structure: {e}")
            return

        # Filter to only conversations marked for export (export defaults to True if not specified)
        channels_to_export = [
            ch for ch in channels_data.get("channels", [])
            if ch.get("export", True) is True
        ]
        
        if not channels_to_export:
            logger.warning("No conversations marked for export. Set 'export: true' in channels.json for conversations you want to export.")
            return
        
        logger.info(f"Found {len(channels_to_export)} conversation(s) to export")

        # Load people.json as a cache/pre-warming mechanism (optional - will lookup on-demand if missing)
        people_cache = {}
        people_json = load_json_file("config/people.json")
        if people_json:
            # Validate people.json structure
            try:
                validate_people_json(people_json)
            except ValueError as e:
                logger.warning(f"Invalid people.json structure: {e}. Will lookup users on-demand from Slack API.")
                people_cache = {}
            else:
                people_cache = {p["slackId"]: p["displayName"] for p in people_json.get("people", [])}
                logger.info(f"Loaded {len(people_cache)} users from people.json cache")
        else:
            logger.info("No people.json found - will lookup users on-demand from Slack API")
        
        # Make output directory configurable
        output_dir = os.getenv('SLACK_EXPORT_OUTPUT_DIR', 'slack_exports')
        
        # Validate output directory path early to prevent path traversal
        original_output_dir = output_dir
        
        # Check original path BEFORE normalization to catch path traversal attempts
        if '..' in original_output_dir:
            logger.error(f"Invalid output directory path detected (contains '..'): {original_output_dir}. Aborting.")
            sys.exit(1)
        
        # Then normalize and resolve
        output_dir = os.path.abspath(os.path.normpath(original_output_dir))
        
        # Optional: Restrict to a safe base directory (current working directory)
        # This prevents writing outside the expected location
        safe_base = os.path.abspath(os.getcwd())
        if not output_dir.startswith(safe_base):
            logger.error(f"Output directory must be within current working directory. Got: {output_dir}, Base: {safe_base}")
            sys.exit(1)
        
        create_directory(output_dir)
        
        # Initialize statistics tracking
        stats = {
            'processed': 0,
            'skipped': 0,
            'failed': 0,
            'uploaded': 0,
            'upload_failed': 0,
            'shared': 0,
            'share_failed': 0,
            'total_messages': 0
        }
        
        total_conversations = len(channels_to_export)
        logger.info(f"Starting export of {total_conversations} conversation(s)")

        for idx, channel_info in enumerate(channels_to_export, 1):
            # Validate channel_info structure
            if not isinstance(channel_info, dict):
                logger.warning(f"Invalid channel info format: {channel_info}. Skipping.")
                stats['skipped'] += 1
                continue
            
            # Add small delay between conversations to avoid rate limits
            if idx > 1:
                time.sleep(CONVERSATION_DELAY_SECONDS)  # Small delay between conversations
            
            # Progress indicator
            logger.info(f"[{idx}/{total_conversations}] Processing conversation...")
            
            channel_id = channel_info.get("id")
            
            # Validate channel ID format
            if not channel_id or not validate_channel_id(channel_id):
                logger.warning(f"Invalid channel ID format: {channel_id}. Skipping.")
                stats['skipped'] += 1
                continue
            
            channel_name = get_conversation_display_name(channel_info, slack_client)
            
            logger.info(f"--- Processing conversation: {channel_name} ({channel_id}) ---")

            # Validate timestamps if provided
            oldest_ts = convert_date_to_timestamp(args.start_date)
            latest_ts = convert_date_to_timestamp(args.end_date, is_end_date=True)
            if args.start_date and oldest_ts is None:
                logger.error(f"Invalid start date format: {args.start_date}")
                stats['skipped'] += 1
                continue
            if args.end_date and latest_ts is None:
                logger.error(f"Invalid end date format: {args.end_date}")
                stats['skipped'] += 1
                continue
            
            # Validate date range logic
            if args.start_date and args.end_date and oldest_ts and latest_ts:
                if float(oldest_ts) > float(latest_ts):
                    logger.error(f"Start date ({args.start_date}) must be before end date ({args.end_date})")
                    stats['skipped'] += 1
                    continue
                
                # Validate date range doesn't exceed maximum
                date_range_days = (float(latest_ts) - float(oldest_ts)) / 86400  # Convert seconds to days
                if date_range_days > MAX_DATE_RANGE_DAYS:
                    logger.error(f"Date range ({date_range_days:.0f} days) exceeds maximum allowed ({MAX_DATE_RANGE_DAYS} days)")
                    stats['skipped'] += 1
                    continue

            history = slack_client.fetch_channel_history(
                channel_id,
                oldest_ts=oldest_ts,
                latest_ts=latest_ts
            )

            if history:
                # Check for input size limits
                if len(history) > MAX_MESSAGES_PER_CONVERSATION:
                    logger.error(f"Conversation {channel_name} exceeds maximum message limit ({MAX_MESSAGES_PER_CONVERSATION}). Skipping.")
                    stats['skipped'] += 1
                    continue
                
                # Warn about large conversations
                if len(history) > LARGE_CONVERSATION_THRESHOLD:
                    logger.warning(f"Large conversation detected ({len(history)} messages). This may take a while and use significant memory.")
                
                processed_history = preprocess_history(history, slack_client, people_cache)
                
                # Check for empty history after processing
                if not processed_history or not processed_history.strip():
                    logger.warning(f"No processable content found for {channel_name}. Skipping file creation.")
                    stats['skipped'] += 1
                    continue
                
                # Add metadata header
                export_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                metadata_header = f"""Slack Conversation Export
Channel: {channel_name}
Channel ID: {channel_id}
Export Date: {export_date}
Total Messages: {len(history)}

{'='*80}

"""
                processed_history = metadata_header + processed_history
                
                # Sanitize filename to prevent path traversal
                safe_channel_name = sanitize_filename(channel_name)
                output_filename = f"{safe_channel_name}_history.txt"
                output_filepath = os.path.join(output_dir, output_filename)
                
                # Additional safety check - ensure path is within output_dir
                abs_output_dir = os.path.abspath(output_dir)
                abs_output_filepath = os.path.abspath(output_filepath)
                if not abs_output_filepath.startswith(abs_output_dir):
                    logger.error(f"Invalid file path detected: {output_filepath}. Skipping.")
                    stats['failed'] += 1
                    continue
                
                try:
                    with open(output_filepath, 'w', encoding='utf-8') as f:
                        f.write(processed_history)
                        f.flush()
                        os.fsync(f.fileno())  # Ensure data is written to disk
                    
                    # Verify file was written successfully and check size
                    if not os.path.exists(output_filepath):
                        logger.error(f"File write verification failed for {output_filepath}")
                        stats['failed'] += 1
                        continue
                    
                    file_size = os.path.getsize(output_filepath)
                    if file_size == 0:
                        logger.error(f"File write verification failed - empty file: {output_filepath}")
                        stats['failed'] += 1
                        continue
                    
                    if file_size > MAX_FILE_SIZE_BYTES:
                        logger.error(f"File size ({file_size / 1024 / 1024:.2f} MB) exceeds maximum ({MAX_FILE_SIZE_MB} MB) for {output_filepath}")
                        stats['failed'] += 1
                        continue
                    
                    stats['processed'] += 1
                    stats['total_messages'] += len(history)
                    logger.info(f"Saved processed history to {output_filepath}")
                except IOError as e:
                    logger.error(f"Failed to write file {output_filepath}: {e}")
                    stats['failed'] += 1
                    continue
                except Exception as e:
                    logger.error(f"Unexpected error writing file {output_filepath}: {e}", exc_info=True)
                    stats['failed'] += 1
                    continue

                if args.upload_to_drive:
                    # Sanitize folder name for Google Drive
                    sanitized_folder_name = sanitize_folder_name(channel_name)
                    folder_id = google_drive_client.create_folder(sanitized_folder_name, google_drive_folder_id)
                    if folder_id:
                        file_id = google_drive_client.upload_file(output_filepath, folder_id)
                        if not file_id:
                            logger.error(f"Failed to upload file for {channel_name}. Skipping sharing.")
                            stats['upload_failed'] += 1
                            continue
                        
                        stats['uploaded'] += 1
                        
                        # Share with members (with rate limiting)
                        members = slack_client.get_channel_members(channel_id)
                        if not members:
                            logger.warning(f"No members found for {channel_name}. Skipping sharing.")
                            continue
                        
                        shared_emails = set()
                        share_errors = []
                        share_failures = 0
                        for i, member_id in enumerate(members):
                            # Rate limit: pause every N shares to avoid API limits
                            if i > 0 and i % SHARE_RATE_LIMIT_INTERVAL == 0:
                                time.sleep(SHARE_RATE_LIMIT_DELAY)
                            
                            user_info = slack_client.get_user_info(member_id)
                            if user_info and user_info.get("email"):
                                email = user_info["email"]
                                # Validate email format
                                if not validate_email(email):
                                    logger.warning(f"Invalid email format: {email}. Skipping.")
                                    continue
                                
                                if email not in shared_emails:
                                    try:
                                        shared = google_drive_client.share_folder(folder_id, email)
                                        if shared:
                                            shared_emails.add(email)
                                            stats['shared'] += 1
                                        else:
                                            share_errors.append(f"{email}: share failed")
                                            share_failures += 1
                                    except Exception as e:
                                        share_errors.append(f"{email}: {str(e)}")
                                        share_failures += 1
                        
                        stats['share_failed'] += share_failures
                        
                        if share_errors:
                            logger.warning(f"Failed to share with some users: {', '.join(share_errors)}")
                        
                        logger.info(f"Shared folder '{sanitized_folder_name}' with {len(shared_emails)} participants")
            else:
                logger.warning(f"No history found for {channel_name} ({channel_id})")
                stats['skipped'] += 1
        
        # Log processing statistics
        logger.info("="*80)
        logger.info("Export Statistics:")
        logger.info(f"  Processed: {stats['processed']}")
        logger.info(f"  Skipped: {stats['skipped']}")
        logger.info(f"  Failed: {stats['failed']}")
        if args.upload_to_drive:
            logger.info(f"  Uploaded to Drive: {stats['uploaded']}")
            logger.info(f"  Upload Failed: {stats['upload_failed']}")
            logger.info(f"  Folders shared: {stats['shared']}")
            logger.info(f"  Share Failed: {stats['share_failed']}")
        logger.info(f"  Total messages processed: {stats['total_messages']}")
        logger.info("="*80)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export Slack conversations and upload to Google Drive.")
    parser.add_argument("--make-ref-files", action="store_true", help="Generate reference files (channels.json, people.json).")
    parser.add_argument("--export-history", action="store_true", help="Export conversation history.")
    parser.add_argument("--upload-to-drive", action="store_true", help="Upload exported files to Google Drive.")
    parser.add_argument("--start-date", help="Start date for history export (YYYY-MM-DD).")
    parser.add_argument("--end-date", help="End date for history export (YYYY-MM-DD).")
    
    args = parser.parse_args()

    if not any([args.make_ref_files, args.export_history, args.upload_to_drive]):
        parser.print_help()
    else:
        main(args)
