import argparse
import os
import re
import sys
import time
from calendar import monthrange
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple, Set

# Add project root to Python path so imports work regardless of how script is invoked
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from dotenv import load_dotenv
from slack_sdk.errors import SlackApiError

from src.google_drive import GoogleDriveClient
from src.slack_client import SlackClient
from src.utils import (
    convert_date_to_timestamp,
    create_directory,
    load_json_file,
    sanitize_filename,
    sanitize_folder_name,
    sanitize_path_for_logging,
    sanitize_string_for_logging,
    save_json_file,
    setup_logging,
    validate_channel_id,
    validate_channels_json,
    validate_email,
    validate_people_json,
)
from src.message_processing import (
    group_messages_by_date,
    preprocess_history,
    should_chunk_export,
    split_messages_by_month,
    estimate_file_size,
    filter_messages_by_date_range,
)
from src.drive_upload import (
    share_folder_with_members,
    share_folder_for_browser_export,
    load_people_cache,
    get_oldest_timestamp_for_export,
    upload_messages_to_drive,
    initialize_stats,
    log_statistics,
)
from src.export_api import get_conversation_display_name
from src.export_browser import (
    load_browser_export_config,
    find_conversation_in_config,
    select_conversation_from_sidebar,
)
from src.cli import (
    BROWSER_EXPORT_CONFIG_KEY,
    BROWSER_EXPORT_CONFIG_FILENAME,
    CHANNELS_CONFIG_FILENAME,
    PEOPLE_CONFIG_FILENAME,
    METADATA_FILE_SUFFIX,
)

# Load environment variables from .env file if it exists
load_dotenv()

logger = setup_logging()

# Constants
CONVERSATION_DELAY_SECONDS = 0.5
LARGE_CONVERSATION_THRESHOLD = 10000
SECONDS_PER_DAY = 86400  # Seconds in a day
BYTES_PER_MB = 1024 * 1024  # Bytes per megabyte

# Configuration file names (imported from cli.py)
# BROWSER_EXPORT_CONFIG_KEY, BROWSER_EXPORT_CONFIG_FILENAME, etc. are imported from cli.py


# Environment variable parsing with validation
def _get_env_int(key: str, default: int, min_val: Optional[int] = None, max_val: Optional[int] = None) -> int:
    """Safely parse integer environment variable with fallback and range validation.
    
    Args:
        key: Environment variable key
        default: Default value if key not found or invalid
        min_val: Optional minimum allowed value
        max_val: Optional maximum allowed value
        
    Returns:
        Parsed integer value, clamped to min_val/max_val if provided
    """
    try:
        value = os.getenv(key)
        if value is None:
            return default
        int_value = int(value)
        
        # Validate range if specified
        if min_val is not None and int_value < min_val:
            logger.warning(f"{key} value {int_value} below minimum {min_val}, using {min_val}")
            return min_val
        if max_val is not None and int_value > max_val:
            logger.warning(f"{key} value {int_value} above maximum {max_val}, using {max_val}")
            return max_val
        
        return int_value
    except ValueError:
        logger.warning(f"Invalid {key} value '{os.getenv(key)}', using default: {default}")
        return default


MAX_FILE_SIZE_MB = _get_env_int("MAX_EXPORT_FILE_SIZE_MB", 100, min_val=1, max_val=1000)
MAX_MESSAGES_PER_CONVERSATION = _get_env_int("MAX_MESSAGES_PER_CONVERSATION", 50000, min_val=1)
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * BYTES_PER_MB
# Maximum date range in days (1 year)
MAX_DATE_RANGE_DAYS = _get_env_int("MAX_DATE_RANGE_DAYS", 365, min_val=1, max_val=3650)
# Chunking thresholds for bulk exports
CHUNK_DATE_RANGE_DAYS = 30  # Chunk if date range exceeds this
CHUNK_MESSAGE_THRESHOLD = 10000  # Chunk if message count exceeds this
# Memory management: chunk size for processing daily message groups in upload_messages_to_drive
DAILY_MESSAGE_CHUNK_SIZE = 10000  # Process daily messages in chunks of this size to manage memory


# Functions moved to message_processing.py, export_api.py, drive_upload.py, export_browser.py
# Imported above


# Functions moved to drive_upload.py, export_api.py, export_browser.py, message_processing.py
# All duplicate function definitions removed - using imports instead


def _validate_and_setup_environment() -> Tuple[SlackClient, GoogleDriveClient, Optional[str]]:
    """Validate environment variables and setup clients.

    Returns:
        Tuple of (slack_client, google_drive_client, google_drive_folder_id)
    """
    # Get configuration from environment variables with validation
    slack_bot_token = os.getenv("SLACK_BOT_TOKEN", "").strip()
    google_drive_credentials_file = os.getenv("GOOGLE_DRIVE_CREDENTIALS_FILE", "").strip()
    google_drive_folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "").strip()

    if not slack_bot_token:
        logger.error(
            "SLACK_BOT_TOKEN environment variable is required and cannot be empty. Exiting."
        )
        sys.exit(1)

    if not google_drive_credentials_file:
        logger.error(
            "GOOGLE_DRIVE_CREDENTIALS_FILE environment variable is required and cannot be empty. Exiting."
        )
        sys.exit(1)

    # Validate and sanitize credentials file path
    try:
        # Resolve to absolute path to prevent traversal
        google_drive_credentials_file = os.path.abspath(
            os.path.expanduser(google_drive_credentials_file)
        )
        if not os.path.exists(google_drive_credentials_file):
            logger.error(
                f"Credentials file not found: {sanitize_path_for_logging(google_drive_credentials_file)}"
            )
            sys.exit(1)
        if not os.path.isfile(google_drive_credentials_file):
            logger.error(
                f"Credentials path is not a file: {sanitize_path_for_logging(google_drive_credentials_file)}"
            )
            sys.exit(1)
        # Check if file is readable
        if not os.access(google_drive_credentials_file, os.R_OK):
            logger.error(
                f"Credentials file is not readable: {sanitize_path_for_logging(google_drive_credentials_file)}"
            )
            sys.exit(1)
    except (OSError, ValueError) as e:
        logger.error(f"Invalid credentials file path: {sanitize_path_for_logging(str(e))}")
        sys.exit(1)

    if not google_drive_folder_id:
        logger.warning("GOOGLE_DRIVE_FOLDER_ID not set. Files will be uploaded to Drive root.")

    slack_client = SlackClient(slack_bot_token)
    google_drive_client = GoogleDriveClient(google_drive_credentials_file)
    return slack_client, google_drive_client, google_drive_folder_id


def _setup_output_directory() -> str:
    """Setup and validate output directory.

    Returns:
        Path to validated output directory
    """
    # Make output directory configurable
    output_dir = os.getenv("SLACK_EXPORT_OUTPUT_DIR", "slack_exports")

    # Validate output directory path early to prevent path traversal
    original_output_dir = output_dir

    # Check original path BEFORE normalization to catch path traversal attempts
    if ".." in original_output_dir:
        logger.error(
            f"Invalid output directory path detected (contains '..'): {original_output_dir}. Aborting."
        )
        sys.exit(1)

    # Then normalize and resolve
    output_dir = os.path.abspath(os.path.normpath(original_output_dir))

    # Optional: Restrict to a safe base directory (current working directory)
    # This prevents writing outside the expected location
    safe_base = os.path.abspath(os.getcwd())
    if not output_dir.startswith(safe_base):
        logger.error(
            f"Output directory must be within current working directory. Got: {output_dir}, Base: {safe_base}"
        )
        sys.exit(1)

    create_directory(output_dir)
    return output_dir


def main(args: argparse.Namespace, mcp_evaluate_script: Callable = None, mcp_click: Callable = None, mcp_press_key: Callable = None, mcp_fill: Callable = None) -> None:
    """Main function to run the Slack history export and upload process."""
    slack_client, google_drive_client, google_drive_folder_id = _validate_and_setup_environment()

    if args.make_ref_files:
        logger.info("Fetching all conversations and users to create reference files...")
        channels = slack_client.get_all_channels()

        # Filter out any direct messages (DMs) - safety check
        channels = [ch for ch in channels if not ch.get("is_im")]

        # Add export flag (defaults to true) to each conversation
        # Preserve existing export and share flags if channels.json already exists
        existing_channels_data = load_json_file("config/channels.json")
        existing_export_map = {}
        existing_share_map = {}
        if existing_channels_data:
            for ch in existing_channels_data.get("channels", []):
                if "id" in ch:
                    existing_export_map[ch["id"]] = ch.get("export", True)
                    existing_share_map[ch["id"]] = ch.get("share", True)

        channels_with_export = []
        for channel in channels:
            channel_entry = dict(channel)
            # Preserve existing export setting, or default to True
            if channel_entry.get("id") in existing_export_map:
                channel_entry["export"] = existing_export_map[channel_entry.get("id")]
            elif "export" not in channel_entry:
                channel_entry["export"] = True
            # Preserve existing share setting, or default to True
            if channel_entry.get("id") in existing_share_map:
                channel_entry["share"] = existing_share_map[channel_entry.get("id")]
            elif "share" not in channel_entry:
                channel_entry["share"] = True
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
        logger.info(
            f"Found {len(channels_with_export)} conversations. Set 'export: false' in channels.json to exclude any you don't want to export."
        )


    if args.export_history:
        channels_data = load_json_file("config/channels.json")
        if not channels_data:
            logger.error("Could not load channels from config/channels.json. Exiting.")
            example_path = "config/channels.json.example"
            if os.path.exists(example_path):
                logger.info(
                    f"Copy {example_path} to config/channels.json and customize it for your needs."
                )
                logger.info(
                    "Alternatively, run with --make-ref-files first to generate channels.json"
                )
            else:
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
            ch for ch in channels_data.get("channels", []) if ch.get("export", True) is True
        ]

        if not channels_to_export:
            logger.warning(
                "No conversations marked for export. Set 'export: true' in channels.json for conversations you want to export."
            )
            return

        logger.info(f"Found {len(channels_to_export)} conversation(s) to export")

        # Load people.json cache and opt-out sets
        people_cache, no_notifications_set, no_share_set, people_json = load_people_cache()

        # Setup output directory
        output_dir = _setup_output_directory()

        # Initialize statistics tracking
        stats = initialize_stats()

        total_conversations = len(channels_to_export)
        logger.info(f"Starting export of {total_conversations} conversation(s)")

        # Override limits if bulk export is enabled
        effective_max_date_range = None if args.bulk_export else MAX_DATE_RANGE_DAYS
        effective_max_messages = None if args.bulk_export else MAX_MESSAGES_PER_CONVERSATION
        effective_max_file_size = None if args.bulk_export else MAX_FILE_SIZE_BYTES

        if args.bulk_export:
            logger.info("Bulk export mode enabled - limits overridden for large exports")

        for idx, channel_info in enumerate(channels_to_export, 1):
            # Validate channel_info structure
            if not isinstance(channel_info, dict):
                logger.warning(f"Invalid channel info format: {channel_info}. Skipping.")
                stats["skipped"] += 1
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
                stats["skipped"] += 1
                continue

            channel_name = get_conversation_display_name(channel_info, slack_client)

            # Cache sanitized names to avoid repeated calculations
            sanitized_names = {
                "folder": sanitize_folder_name(channel_name),
                "file": sanitize_filename(channel_name),
            }

            logger.info(f"--- Processing conversation: {channel_name} ({channel_id}) ---")

            # Determine oldest timestamp for incremental fetching
            sanitized_folder_name = sanitized_names["folder"]
            safe_channel_name = sanitized_names["file"]
            
            # Get folder ID early if uploading to Drive (needed for incremental export check)
            folder_id = None
            if args.upload_to_drive:
                folder_id = google_drive_client.create_folder(
                    sanitized_folder_name, google_drive_folder_id
                )

            oldest_ts = get_oldest_timestamp_for_export(
                google_drive_client=google_drive_client if args.upload_to_drive else None,
                folder_id=folder_id,
                conversation_name=channel_name,
                explicit_start_date=args.start_date,
                upload_to_drive=args.upload_to_drive,
                sanitized_folder_name=sanitized_folder_name,
                safe_conversation_name=safe_channel_name,
            )
            
            if args.start_date and oldest_ts is None:
                # Invalid start date format - skip this conversation
                stats["skipped"] += 1
                continue

            # Validate end date if provided
            latest_ts = convert_date_to_timestamp(args.end_date, is_end_date=True)
            if args.end_date and latest_ts is None:
                logger.error(f"Invalid end date format: {args.end_date}")
                stats["skipped"] += 1
                continue

            # Validate date range (for API exports, filtering happens at fetch time via timestamps)
            # Use filter function for validation only
            _, error_msg = filter_messages_by_date_range(
                messages=[],  # Empty list - we're just validating, not filtering
                oldest_ts=oldest_ts,
                latest_ts=latest_ts,
                validate_range=True,
                max_date_range_days=effective_max_date_range,
            )

            if error_msg:
                # Format error message with user-friendly dates
                if "Start date" in error_msg:
                    error_msg = error_msg.replace(
                        f"Start date ({oldest_ts})",
                        f"Start date ({args.start_date or 'last export'})"
                    ).replace(f"end date ({latest_ts})", f"end date ({args.end_date})")
                logger.error(error_msg)
                stats["skipped"] += 1
                continue

            history = slack_client.fetch_channel_history(
                channel_id, oldest_ts=oldest_ts, latest_ts=latest_ts
            )

            if history is None:
                logger.error(
                    f"Failed to fetch history for {channel_name} ({channel_id}) - API error"
                )
                stats["failed"] += 1
                continue

            # --- Orphan Thread Detection & Fetching ---
            # Identify replies whose root messages are missing from the current history batch
            # (i.e., threads that started before the export window but have activity now)
            if history:
                messages_by_ts = {msg.get("ts"): msg for msg in history if msg.get("ts")}
                orphan_threads = set()

                for msg in history:
                    thread_ts = msg.get("thread_ts")
                    ts = msg.get("ts")
                    
                    # Check if it's a reply (has thread_ts and it differs from its own ts)
                    if thread_ts and ts != thread_ts:
                        # If the parent thread_ts is NOT in our current message set, it's an orphan reply
                        if thread_ts not in messages_by_ts:
                            orphan_threads.add(thread_ts)

                if orphan_threads:
                    logger.info(f"Found {len(orphan_threads)} active threads starting before export window. Fetching full context...")
                    
                    for thread_ts in orphan_threads:
                        logger.info(f"Fetching full history for active thread {thread_ts}...")
                        thread_messages = slack_client.fetch_thread_history(channel_id, thread_ts)
                        
                        if thread_messages:
                            # Add messages to history, avoiding duplicates
                            for t_msg in thread_messages:
                                t_ts = t_msg.get("ts")
                                if t_ts and t_ts not in messages_by_ts:
                                    history.append(t_msg)
                                    messages_by_ts[t_ts] = t_msg # Update lookup
                        else:
                            logger.warning(f"Failed to fetch thread {thread_ts}")

                    # Re-sort history after adding thread messages
                    history.sort(key=lambda x: float(x.get("ts", 0)))
                    logger.info(f"Export history expanded to {len(history)} messages after active thread retrieval")

            if len(history) == 0:
                logger.info(
                    f"No messages found for {channel_name} ({channel_id}) in specified date range"
                )
                stats["skipped"] += 1
                continue

            # Check for input size limits (unless bulk export)
            if effective_max_messages and len(history) > effective_max_messages:
                logger.error(
                    f"Conversation {channel_name} exceeds maximum message limit ({effective_max_messages}). Use --bulk-export to override."
                )
                stats["skipped"] += 1
                continue

            # Warn about large conversations
            if len(history) > LARGE_CONVERSATION_THRESHOLD:
                logger.warning(
                    f"Large conversation detected ({len(history)} messages). This may take a while and use significant memory."
                )

            # Upload to Google Drive if requested
            if args.upload_to_drive:
                # Upload messages using unified function
                upload_stats = upload_messages_to_drive(
                    messages=history,
                    conversation_name=channel_name,
                    conversation_id=channel_id,
                    google_drive_client=google_drive_client,
                    google_drive_folder_id=google_drive_folder_id,
                    slack_client=slack_client,
                    people_cache=people_cache,
                    use_display_names=False,
                    stats=stats,
                )

                # Update stats with upload results
                stats.update(upload_stats)

                # Get folder ID for sharing (needed for share_folder_with_members)
                sanitized_folder_name = sanitized_names["folder"]
                folder_id = google_drive_client.create_folder(
                    sanitized_folder_name, google_drive_folder_id
                )

                if folder_id:
                    # Share folder with members
                    share_folder_with_members(
                        google_drive_client,
                        folder_id,
                        slack_client,
                        channel_id,
                        channel_name,
                        channel_info,
                        no_notifications_set,
                        no_share_set,
                        stats,
                        sanitized_folder_name=sanitized_folder_name,
                        people_cache=people_cache,
                        people_json=people_json,
                    )
                else:
                    logger.warning(f"Could not get folder ID for sharing {channel_name}")

                continue  # Skip file-based export when uploading to Drive

            # Determine if we should chunk this export (for local file exports)
            should_chunk = should_chunk_export(history, oldest_ts, latest_ts, args.bulk_export)

            if should_chunk:
                logger.info(
                    f"Large export detected - splitting into monthly chunks for {channel_name}"
                )
                chunks = split_messages_by_month(history)
                logger.info(f"Split into {len(chunks)} monthly chunk(s)")

                # Process each chunk
                chunk_files = []
                for chunk_idx, (chunk_start, chunk_end, chunk_messages) in enumerate(chunks, 1):
                    logger.info(
                        f"Processing chunk {chunk_idx}/{len(chunks)}: {chunk_start.strftime('%Y-%m')} ({len(chunk_messages)} messages)"
                    )

                    processed_history = preprocess_history(
                        chunk_messages, slack_client, people_cache
                    )

                    # Check for empty history after processing
                    if not processed_history or not processed_history.strip():
                        logger.warning(
                            f"No processable content found for chunk {chunk_idx} of {channel_name}. Skipping."
                        )
                        continue

                    # Add metadata header for chunk
                    export_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                    date_range_str = (
                        f"{chunk_start.strftime('%Y-%m-%d')} to {chunk_end.strftime('%Y-%m-%d')}"
                    )
                    metadata_header = f"""Slack Conversation Export
Channel: {channel_name}
Channel ID: {channel_id}
Export Date: {export_date}
Date Range: {date_range_str}
Total Messages: {len(chunk_messages)}
Chunk: {chunk_idx} of {len(chunks)}

{'='*80}

"""
                    processed_history = metadata_header + processed_history

                    # Estimate file size
                    estimated_size = estimate_file_size(processed_history)
                    if effective_max_file_size and estimated_size > effective_max_file_size:
                        logger.warning(
                            f"Estimated file size ({estimated_size / 1024 / 1024:.2f} MB) exceeds maximum ({effective_max_file_size / 1024 / 1024:.2f} MB) for chunk {chunk_idx}. File will still be created."
                        )

                    # Create filename with date range
                    safe_channel_name = sanitized_names["file"]
                    month_str = chunk_start.strftime("%Y-%m")
                    export_datetime = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
                    output_filename = (
                        f"{safe_channel_name}_history_{month_str}_{export_datetime}.txt"
                    )
                    output_filepath = os.path.join(output_dir, output_filename)

                    # Additional safety check - ensure path is within output_dir
                    abs_output_dir = os.path.abspath(output_dir)
                    abs_output_filepath = os.path.abspath(output_filepath)
                    if not abs_output_filepath.startswith(abs_output_dir):
                        logger.error(
                            f"Invalid file path detected: {output_filepath}. Skipping chunk {chunk_idx}."
                        )
                        stats["failed"] += 1
                        continue

                    try:
                        with open(output_filepath, "w", encoding="utf-8") as f:
                            f.write(processed_history)
                            f.flush()
                            os.fsync(f.fileno())  # Ensure data is written to disk

                        # Verify file was written successfully and check size
                        if not os.path.exists(output_filepath):
                            logger.error(f"File write verification failed for {output_filepath}")
                            stats["failed"] += 1
                            continue

                        file_size = os.path.getsize(output_filepath)
                        if file_size == 0:
                            logger.error(
                                f"File write verification failed - empty file: {output_filepath}"
                            )
                            stats["failed"] += 1
                            continue

                        if effective_max_file_size and file_size > effective_max_file_size:
                            logger.warning(
                                f"File size ({file_size / 1024 / 1024:.2f} MB) exceeds maximum ({effective_max_file_size / 1024 / 1024:.2f} MB) for {output_filepath}. File created but may cause issues."
                            )

                        chunk_files.append((output_filepath, chunk_messages))
                        stats["processed"] += 1
                        stats["total_messages"] += len(chunk_messages)
                        logger.info(
                            f"Saved chunk {chunk_idx} to {output_filepath} ({file_size / 1024 / 1024:.2f} MB)"
                        )
                    except IOError as e:
                        logger.error(f"Failed to write file {output_filepath}: {e}")
                        stats["failed"] += 1
                        continue
                    except Exception as e:
                        logger.error(
                            f"Unexpected error writing file {output_filepath}: {e}", exc_info=True
                        )
                        stats["failed"] += 1
                        continue

                # Upload chunked files to Drive if requested
                if args.upload_to_drive and chunk_files:
                    sanitized_folder_name = sanitized_names["folder"]
                    safe_channel_name = sanitized_names["file"]
                    folder_id = google_drive_client.create_folder(
                        sanitized_folder_name, google_drive_folder_id
                    )
                    if folder_id:
                        for chunk_filepath, chunk_messages in chunk_files:
                            # Read the file content
                            try:
                                with open(chunk_filepath, "r", encoding="utf-8") as f:
                                    doc_content = f.read()

                                # Extract doc name from filename (remove .txt extension)
                                doc_name = os.path.basename(chunk_filepath).replace(".txt", "")

                                # Create or update Google Doc
                                doc_id = google_drive_client.create_or_update_google_doc(
                                    doc_name, doc_content, folder_id, overwrite=False
                                )
                                if not doc_id:
                                    logger.error(
                                        f"Failed to create Google Doc for chunk {chunk_filepath}"
                                    )
                                    stats["upload_failed"] += 1
                                else:
                                    stats["uploaded"] += 1
                            except IOError as e:
                                logger.error(f"Failed to read chunk file {chunk_filepath}: {e}")
                                stats["upload_failed"] += 1

                        # Save export metadata with latest timestamp from all chunks
                        if history:
                            latest_message_ts = max(float(msg.get("ts", 0)) for msg in history)
                            google_drive_client.save_export_metadata(
                                folder_id, safe_channel_name, str(latest_message_ts)
                            )
                            logger.info(f"Saved export metadata for {channel_name}")

                        # Share folder with members
                        share_folder_with_members(
                            google_drive_client,
                            folder_id,
                            slack_client,
                            channel_id,
                            channel_name,
                            channel_info,
                            no_notifications_set,
                            no_share_set,
                            stats,
                            sanitized_folder_name=sanitized_names["folder"],
                            people_cache=people_cache,
                            people_json=people_json,
                        )
                    continue  # Skip single file processing for chunked exports

            # Single file export (non-chunked)
            processed_history = preprocess_history(history, slack_client, people_cache)

            # Check for empty history after processing
            if not processed_history or not processed_history.strip():
                logger.warning(
                    f"No processable content found for {channel_name}. Skipping file creation."
                )
                stats["skipped"] += 1
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

            # Estimate file size before writing
            estimated_size = estimate_file_size(processed_history)
            if effective_max_file_size and estimated_size > effective_max_file_size:
                logger.warning(
                    f"Estimated file size ({estimated_size / 1024 / 1024:.2f} MB) exceeds maximum ({effective_max_file_size / 1024 / 1024:.2f} MB). File will still be created."
                )

            # Use cached sanitized names
            safe_channel_name = sanitized_names["file"]
            export_datetime = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
            output_filename = f"{safe_channel_name}_history_{export_datetime}.txt"
            output_filepath = os.path.join(output_dir, output_filename)

            # Additional safety check - ensure path is within output_dir
            abs_output_dir = os.path.abspath(output_dir)
            abs_output_filepath = os.path.abspath(output_filepath)
            if not abs_output_filepath.startswith(abs_output_dir):
                logger.error(f"Invalid file path detected: {output_filepath}. Skipping.")
                stats["failed"] += 1
                continue

            try:
                with open(output_filepath, "w", encoding="utf-8") as f:
                    f.write(processed_history)
                    f.flush()
                    os.fsync(f.fileno())  # Ensure data is written to disk

                # Verify file was written successfully and check size
                if not os.path.exists(output_filepath):
                    logger.error(f"File write verification failed for {output_filepath}")
                    stats["failed"] += 1
                    continue

                file_size = os.path.getsize(output_filepath)
                if file_size == 0:
                    logger.error(f"File write verification failed - empty file: {output_filepath}")
                    stats["failed"] += 1
                    continue

                if effective_max_file_size and file_size > effective_max_file_size:
                    logger.warning(
                        f"File size ({file_size / 1024 / 1024:.2f} MB) exceeds maximum ({effective_max_file_size / 1024 / 1024:.2f} MB) for {output_filepath}. File created but may cause issues."
                    )

                stats["processed"] += 1
                stats["total_messages"] += len(history)
                logger.info(f"Saved processed history to {output_filepath}")
            except IOError as e:
                logger.error(f"Failed to write file {output_filepath}: {e}")
                stats["failed"] += 1
                continue
            except Exception as e:
                logger.error(f"Unexpected error writing file {output_filepath}: {e}", exc_info=True)
                stats["failed"] += 1
                continue

            if args.upload_to_drive:
                # Use cached sanitized names
                sanitized_folder_name = sanitized_names["folder"]
                safe_channel_name = sanitized_names["file"]
                folder_id = google_drive_client.create_folder(
                    sanitized_folder_name, google_drive_folder_id
                )
                if folder_id:
                    # Read the file content
                    try:
                        with open(output_filepath, "r", encoding="utf-8") as f:
                            doc_content = f.read()

                        # Extract doc name from filename (remove .txt extension)
                        doc_name = os.path.basename(output_filepath).replace(".txt", "")

                        # Create or update Google Doc
                        doc_id = google_drive_client.create_or_update_google_doc(
                            doc_name, doc_content, folder_id, overwrite=False
                        )
                        if not doc_id:
                            logger.error(
                                f"Failed to create Google Doc for {channel_name}. Skipping sharing."
                            )
                            stats["upload_failed"] += 1
                            continue

                        stats["uploaded"] += 1
                    except IOError as e:
                        logger.error(f"Failed to read file {output_filepath}: {e}")
                        stats["upload_failed"] += 1
                        continue

                    # Save export metadata to Drive (stateless - works in CI/CD)
                    # Use the latest message timestamp, or current time if no messages
                    if history:
                        latest_message_ts = max(float(msg.get("ts", 0)) for msg in history)
                        google_drive_client.save_export_metadata(
                            folder_id, safe_channel_name, str(latest_message_ts)
                        )
                        logger.info(f"Saved export metadata for {channel_name}")
                    else:
                        google_drive_client.save_export_metadata(
                            folder_id,
                            safe_channel_name,
                            str(datetime.now(timezone.utc).timestamp()),
                        )

                    # Share with members
                    share_folder_with_members(
                        google_drive_client,
                        folder_id,
                        slack_client,
                        channel_id,
                        channel_name,
                        channel_info,
                        no_notifications_set,
                        no_share_set,
                        stats,
                        sanitized_folder_name=sanitized_names["folder"],
                    )

        # Log processing statistics
        log_statistics(stats, args.upload_to_drive)

    elif args.browser_export_dm:
        # Handle browser-based DM export
        # This uses the same code path as --export-history but extracts messages directly from DOM
        from src.browser_response_processor import BrowserResponseProcessor
        from scripts.extract_active_threads import extract_active_threads_for_daily_export
        from scripts.extract_historical_threads import extract_historical_threads_via_search
        import json

        # Require browser-export.json config file
        if not args.browser_export_config:
            logger.error(
                "ERROR: --browser-export-config is required for browser exports."
            )
            logger.error(
                f"Browser exports require {BROWSER_EXPORT_CONFIG_FILENAME} to ensure consistent naming and sharing."
            )
            logger.error(
                f"Example: --browser-export-config config/{BROWSER_EXPORT_CONFIG_FILENAME}"
            )
            sys.exit(1)
        
        # Load browser-export.json config
        config_data = load_browser_export_config(args.browser_export_config)
        if not config_data:
            logger.error(
                f"ERROR: Failed to load {BROWSER_EXPORT_CONFIG_FILENAME} from {args.browser_export_config}"
            )
            logger.error(
                f"Ensure the file exists and has valid JSON structure with '{BROWSER_EXPORT_CONFIG_KEY}' array."
            )
            sys.exit(1)
        
        # Check if MCP tools are available for browser exports
        if args.browser_export_dm and (mcp_evaluate_script is None or mcp_click is None or mcp_press_key is None or mcp_fill is None):
            logger.error(
                "ERROR: --browser-export-dm requires MCP browser automation tools (evaluate_script, click, press_key, fill) to be provided."
            )
            logger.error(
                "When running via an agent, these are automatically passed. When running standalone, ensure appropriate mocks or a compatible environment."
            )

            sys.exit(1)
        
        # Find conversation in config by ID or name
        conversation_info = None
        if args.browser_conversation_id:
            conversation_info = find_conversation_in_config(config_data, conversation_id=args.browser_conversation_id)
        if not conversation_info and args.browser_conversation_name and args.browser_conversation_name != "DM":
            conversation_info = find_conversation_in_config(config_data, conversation_name=args.browser_conversation_name)
        
        if not conversation_info:
            logger.error(
                f"ERROR: Conversation not found in {BROWSER_EXPORT_CONFIG_FILENAME}"
            )
            if args.browser_conversation_id:
                logger.error(f"  Searched by ID: {args.browser_conversation_id}")
            if args.browser_conversation_name and args.browser_conversation_name != "DM":
                logger.error(f"  Searched by name: {args.browser_conversation_name}")
            logger.error(
                f"Ensure the conversation exists in {BROWSER_EXPORT_CONFIG_FILENAME} with matching ID or name."
            )
            sys.exit(1)
        
        # Always use conversation name and ID from config (ensures consistency)
        conversation_name = conversation_info.get("name")
        if not conversation_name:
            logger.error(
                f"ERROR: Conversation in {BROWSER_EXPORT_CONFIG_FILENAME} is missing 'name' field"
            )
            sys.exit(1)
        
        # Always use ID from config
        args.browser_conversation_id = conversation_info.get("id")
        if not args.browser_conversation_id:
            logger.error(
                f"ERROR: Conversation in {BROWSER_EXPORT_CONFIG_FILENAME} is missing 'id' field"
            )

            sys.exit(1)
        
        logger.info(f"Using conversation from config: {conversation_name} ({args.browser_conversation_id})")
        
        # Warn if user provided --browser-conversation-name that doesn't match config
        if args.browser_conversation_name and args.browser_conversation_name != "DM" and args.browser_conversation_name != conversation_name:
            logger.warning(
                f"Provided --browser-conversation-name '{args.browser_conversation_name}' doesn't match config name '{conversation_name}'. "
                f"Using config name '{conversation_name}' for consistency."
            )
        
        # If --select-conversation is enabled, select conversation from sidebar
        if args.select_conversation:
            if not args.browser_conversation_id:
                logger.warning("--select-conversation enabled but no conversation ID found. Skipping selection.")
                logger.warning("Provide --browser-conversation-id or use --browser-export-config to enable automatic selection.")
            else:
                logger.info(f"Selecting conversation {args.browser_conversation_id} from sidebar...")
                # Note: Actual selection will be done by agent using MCP chrome-devtools tools
                # This is a placeholder - the agent should implement the selection logic
                # If selection fails, the agent should log a warning but continue with extraction
                try:
                    select_conversation_from_sidebar(args.browser_conversation_id, mcp_click=mcp_click, mcp_evaluate_script=mcp_evaluate_script)
                except Exception as e:
                    logger.warning(f"Failed to select conversation from sidebar: {e}", exc_info=True)
                    logger.warning("Continuing with extraction - ensure browser is positioned on the correct conversation.")

        logger.info("Browser-based DM export mode (DOM extraction)")
        logger.info(f"Conversation name: {conversation_name}")
        logger.info("Reading messages from stdin (no intermediate files)")

        # Initialize processor for conversation filtering only
        processor = BrowserResponseProcessor()
        
        # Extract messages from DOM (main conversation history)
        # Messages must be provided via stdin (JSON format) - no intermediate files
        # Browser exports use the same code path as --export-history
        main_conversation_messages = []
        
        # Read messages from stdin (required - no file fallback)
        if sys.stdin.isatty():  # stdin is a TTY (no data piped)
            logger.error("No messages provided. Messages must be piped via stdin.")
            logger.info("")
            logger.info("To extract messages from DOM:")
            logger.info("1. Open Slack in a browser and navigate to the conversation")
            logger.info("2. Scroll to load all messages in the date range")
            logger.info("3. Use MCP chrome-devtools tools to run DOM extraction")
            logger.info("   Example: Use mcp_chrome-devtools_evaluate_script with extract_messages_from_dom_script()")
            logger.info("4. Pipe JSON to this script:")
            logger.info("   python scripts/extract_dom_messages.py --output-to-stdout | \\")
            logger.info("     python src/main.py --browser-export-dm --browser-conversation-name 'Name' --upload-to-drive")
            logger.info("")
            logger.info("Browser exports use the same file conventions as --export-history:")
            logger.info("  - File naming: {conversation_name} slack messages {YYYYMMDD}")
            logger.info("  - Same grouping and formatting logic")
            logger.info("  - No intermediate files needed")
            logger.info("")
            logger.info("See ReadMe.md for detailed instructions.")
            sys.exit(1)
        
        try:
            logger.info("Reading messages from stdin...")
            stdin_data = sys.stdin.read()
            if not stdin_data.strip():
                logger.error("No data received from stdin")
                sys.exit(1)
            
            response_data = json.loads(stdin_data)
            main_conversation_messages = response_data.get("messages", [])
            logger.info(f"Loaded {len(main_conversation_messages)} messages from stdin")
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON from stdin: {e}")
            sys.exit(1)
        except Exception as e:
            logger.error(f"Failed to read from stdin: {e}", exc_info=True)
            sys.exit(1)
        
        if not main_conversation_messages and not args.extract_active_threads and not args.extract_historical_threads:
            logger.error("No messages found in input and thread extraction flags are not enabled.")
            sys.exit(1)

        # Filter messages by conversation participants (browser exports may contain multiple conversations)
        if main_conversation_messages:
            main_conversation_messages = processor._filter_by_conversation_participants(main_conversation_messages, conversation_name)
            if not main_conversation_messages:
                logger.warning("No messages found after filtering main conversation by participants.")
        
        # --- Handle Active Thread Extraction ---
        active_thread_messages = []
        if args.extract_active_threads:
            if args.upload_to_drive: # Only attempt if uploading to Drive
                logger.info("Attempting to extract active threads from browser.")
                try:
                    # mcp_chrome-devtools_evaluate_script, mcp_chrome-devtools_click, mcp_chrome-devtools_press_key
                    # These are available globally when running from Cursor/MCP.
                    active_thread_messages = extract_active_threads_for_daily_export(
                        mcp_evaluate_script=mcp_evaluate_script,
                        mcp_click=mcp_click,
                        mcp_press_key=mcp_press_key,
                        target_conversation_name=conversation_name,
                        export_date=datetime.now(timezone.utc), # Export for today and yesterday
                    )
                    logger.info(f"Collected {len(active_thread_messages)} messages from active threads.")
                except Exception as e:
                    logger.error(f"Failed to extract active threads: {e}", exc_info=True)
            else:
                logger.warning("--extract-active-threads is only supported with --upload-to-drive. Skipping thread extraction.")
        
        # --- Handle Historical Thread Extraction (Search) ---
        historical_thread_messages = []
        if args.extract_historical_threads:
            if args.upload_to_drive:
                logger.info("Attempting to extract historical threads via search.")
                try:
                    # Initialize Google Drive client for thread archiving
                    google_drive_credentials_file = os.getenv("GOOGLE_DRIVE_CREDENTIALS_FILE", "").strip()
                    if not google_drive_credentials_file:
                        logger.error("GOOGLE_DRIVE_CREDENTIALS_FILE environment variable is required.")
                        sys.exit(1)
                    
                    try:
                        google_drive_credentials_file = os.path.abspath(os.path.expanduser(google_drive_credentials_file))
                        if not os.path.exists(google_drive_credentials_file):
                            logger.error(f"Credentials file not found: {sanitize_path_for_logging(google_drive_credentials_file)}")
                            sys.exit(1)
                    except (OSError, ValueError) as e:
                        logger.error(f"Invalid credentials file path: {sanitize_path_for_logging(str(e))}")
                        sys.exit(1)

                    archive_drive_client = GoogleDriveClient(google_drive_credentials_file)
                    sanitized_folder_name = sanitize_folder_name(conversation_name)
                    archive_folder_id = archive_drive_client.create_folder(
                        sanitized_folder_name, os.getenv("GOOGLE_DRIVE_FOLDER_ID", "").strip() or None
                    )
                    
                    if not archive_folder_id:
                        logger.error(f"Failed to create/get folder for {conversation_name} during thread archiving")
                        sys.exit(1)

                    search_query = args.search_query
                    if not search_query:
                        # Construct query
                        # Quote conversation name to handle spaces
                        query_parts = [f'in:"{conversation_name}"']
                        if args.start_date:
                            query_parts.append(f'after:{args.start_date}')
                        if args.end_date:
                            query_parts.append(f'before:{args.end_date}')
                        query_parts.append('is:thread')
                        search_query = " ".join(query_parts)
                    
                    # Convert date strings to datetime for the extractor range check
                    start_dt = datetime.strptime(args.start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc) if args.start_date else datetime.min.replace(tzinfo=timezone.utc)
                    end_dt = datetime.strptime(args.end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc) if args.end_date else datetime.max.replace(tzinfo=timezone.utc)

                    extracted_threads = extract_historical_threads_via_search(
                        mcp_evaluate_script=mcp_evaluate_script,
                        mcp_click=mcp_click,
                        mcp_press_key=mcp_press_key,
                        mcp_fill=mcp_fill,
                        search_query=search_query,
                        export_date_range=(start_dt, end_dt)
                    )
                    
                    logger.info(f"Extracted {len(extracted_threads)} threads.")
                    
                    # Process threads: Archive individually and flatten for daily logs
                    for thread_msgs in extracted_threads:
                        # Archive individual thread
                        if thread_msgs:
                            archive_drive_client.upload_thread_doc(archive_folder_id, thread_msgs, conversation_name)
                            # Add to flat list for daily log processing
                            historical_thread_messages.extend(thread_msgs)
                            
                    logger.info(f"Flattened {len(historical_thread_messages)} messages from historical threads for daily logs.")
                except Exception as e:
                    logger.error(f"Failed to extract historical threads: {e}", exc_info=True)
            else:
                logger.warning("--extract-historical-threads is only supported with --upload-to-drive.")

        # Combine and deduplicate all messages from main conversation and active threads
        all_messages_map = {msg.get("ts"): msg for msg in main_conversation_messages if msg.get("ts")}
        for msg in active_thread_messages + historical_thread_messages:
            ts = msg.get("ts")
            if ts and ts not in all_messages_map:
                all_messages_map[ts] = msg
        all_messages = list(all_messages_map.values())
        
        # Sort combined messages chronologically
        all_messages.sort(key=lambda m: float(m.get("ts", 0)))
        
        if not all_messages:
            logger.warning("No messages found from main conversation or active threads.")
            sys.exit(1)

        # Determine oldest timestamp for incremental fetching
        # Initialize Google Drive client early if uploading to Drive (needed for incremental export check)
        browser_google_drive_client = None
        sanitized_folder_name = None
        safe_conversation_name = None
        browser_google_drive_folder_id = None
        browser_folder_id = None
        
        if args.upload_to_drive:
            # Initialize Google Drive client early to check for metadata
            google_drive_credentials_file = os.getenv("GOOGLE_DRIVE_CREDENTIALS_FILE", "").strip()
            if not google_drive_credentials_file:
                logger.error(
                    "GOOGLE_DRIVE_CREDENTIALS_FILE environment variable is required for --upload-to-drive"
                )
                sys.exit(1)

            try:
                google_drive_credentials_file = os.path.abspath(
                    os.path.expanduser(google_drive_credentials_file)
                )
                if not os.path.exists(google_drive_credentials_file):
                    logger.error(
                        f"Credentials file not found: {sanitize_path_for_logging(google_drive_credentials_file)}"
                    )
                    sys.exit(1)
            except (OSError, ValueError) as e:
                logger.error(f"Invalid credentials file path: {sanitize_path_for_logging(str(e))}")
                sys.exit(1)

            browser_google_drive_client = GoogleDriveClient(google_drive_credentials_file)
            sanitized_folder_name = sanitize_folder_name(conversation_name)
            safe_conversation_name = sanitize_filename(conversation_name)
            browser_google_drive_folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "").strip() or None
            
            # Create or get folder to check for metadata
            browser_folder_id = browser_google_drive_client.create_folder(
                sanitized_folder_name, browser_google_drive_folder_id
            )
        
        # Get oldest timestamp using unified function
        oldest_ts = get_oldest_timestamp_for_export(
            google_drive_client=browser_google_drive_client,
            folder_id=browser_folder_id,
            conversation_name=conversation_name,
            explicit_start_date=args.start_date,
            upload_to_drive=args.upload_to_drive,
            sanitized_folder_name=sanitized_folder_name,
            safe_conversation_name=safe_conversation_name,
        )
        
        if args.start_date and oldest_ts is None:
            # Invalid start date format
            logger.error(f"Invalid start date format: {args.start_date}")
            sys.exit(1)

        # Validate and filter messages by date range
        latest_ts = None
        if args.end_date:
            latest_ts = convert_date_to_timestamp(args.end_date, is_end_date=True)
            if latest_ts is None:
                logger.error(f"Invalid end date format: {args.end_date}")
                sys.exit(1)
            logger.info(f"Filtering messages until: {args.end_date} ({latest_ts})")

        # Filter messages by date range (validation happens inside function)
        filtered_messages, error_msg = filter_messages_by_date_range(
            messages=all_messages,
            oldest_ts=oldest_ts,
            latest_ts=latest_ts,
            validate_range=True,
            max_date_range_days=None,  # Browser exports don't validate against MAX_DATE_RANGE_DAYS
        )

        if error_msg:
            logger.error(error_msg)
            sys.exit(1)

        all_messages = filtered_messages

        if not all_messages:
            logger.warning("No messages found after date range filtering")
            sys.exit(1)

        # Check if uploading to Google Drive
        if args.upload_to_drive:
            # Google Drive client may have been initialized earlier for incremental export check
            if browser_google_drive_client is None:
                # Validate Google Drive setup
                google_drive_credentials_file = os.getenv("GOOGLE_DRIVE_CREDENTIALS_FILE", "").strip()
                if not google_drive_credentials_file:
                    logger.error(
                        "GOOGLE_DRIVE_CREDENTIALS_FILE environment variable is required for --upload-to-drive"
                    )
                    sys.exit(1)

                try:
                    google_drive_credentials_file = os.path.abspath(
                        os.path.expanduser(google_drive_credentials_file)
                    )
                    if not os.path.exists(google_drive_credentials_file):
                        logger.error(
                            f"Credentials file not found: {sanitize_path_for_logging(google_drive_credentials_file)}"
                        )
                        sys.exit(1)
                except (OSError, ValueError) as e:
                    logger.error(f"Invalid credentials file path: {sanitize_path_for_logging(str(e))}")
                    sys.exit(1)

                browser_google_drive_client = GoogleDriveClient(google_drive_credentials_file)
                sanitized_folder_name = sanitize_folder_name(conversation_name)
                safe_conversation_name = sanitize_filename(conversation_name)
                browser_google_drive_folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "").strip() or None

            # Create or get folder (may have been created earlier for metadata check)
            browser_folder_id = browser_google_drive_client.create_folder(
                sanitized_folder_name, browser_google_drive_folder_id
            )
            if not browser_folder_id:
                logger.error(f"Failed to create/get folder for {conversation_name}")
                sys.exit(1)

            logger.info(f"Using folder: {sanitized_folder_name} ({browser_folder_id})")

            # Upload messages using unified function
            stats = upload_messages_to_drive(
                messages=all_messages,
                conversation_name=conversation_name,
                conversation_id=args.browser_conversation_id,
                google_drive_client=browser_google_drive_client,
                google_drive_folder_id=browser_google_drive_folder_id,
                slack_client=None, # Not used for browser exports
                people_cache=None, # Not used for browser exports
                use_display_names=True,
            )

            # Share folder with members (same logic as Slack export)
            if conversation_info and browser_folder_id:
                # Initialize Slack client for sharing (required for member lookup)
                slack_bot_token = os.getenv("SLACK_BOT_TOKEN", "").strip()
                if slack_bot_token:
                    try:
                        browser_slack_client = SlackClient(slack_bot_token)
                        # Load people cache and opt-out sets
                        people_cache, no_notifications_set, no_share_set, people_json = load_people_cache()
                        
                        # Add sharing stats to stats dict
                        stats["shared"] = 0
                        stats["share_failed"] = 0
                        
                        # Share folder using same logic as Slack export
                        share_folder_for_browser_export(
                            browser_google_drive_client,
                            browser_folder_id,
                            browser_slack_client,
                            conversation_info,
                            conversation_name,
                            no_notifications_set,
                            no_share_set,
                            stats,
                            people_cache=people_cache,
                            people_json=people_json,
                        )
                    except Exception as e:
                        logger.warning(f"Failed to share folder (Slack client error): {e}", exc_info=True)
                else:
                    logger.warning("SLACK_BOT_TOKEN not set - skipping folder sharing. Set token to enable sharing.")

            # Log statistics
            log_statistics(stats, upload_to_drive=True)

        else:
            # Local file export - use same logic as main export but write to files
            # Group messages by date
            daily_groups = group_messages_by_date(all_messages)
            logger.info(
                f"Grouped {len(all_messages)} messages into {len(daily_groups)} daily group(s)"
            )

            if not daily_groups:
                logger.warning("No messages found to export")
                sys.exit(1)

            # Setup output directory
            output_dir = _setup_output_directory()

            # Write each day to a file - same naming convention as main export
            stats = {
                "processed": 0,
                "total_messages": 0,
            }

            sorted_dates = sorted(daily_groups.keys())
            for date_key in sorted_dates:
                daily_messages = daily_groups[date_key]
                logger.info(f"Processing {len(daily_messages)} messages for date {date_key}")

                # Process messages - use preprocess_history with use_display_names=True
                processed_messages = preprocess_history(
                    daily_messages, slack_client=None, people_cache=None, use_display_names=True
                )

                if not processed_messages or not processed_messages.strip():
                    logger.warning(
                        f"No processable content found for {date_key} of {conversation_name}. Skipping."
                    )
                    continue

                # Add metadata header (same format as main export)
                export_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
                date_obj = datetime.strptime(date_key, "%Y%m%d").replace(tzinfo=timezone.utc)
                date_display = date_obj.strftime("%Y-%m-%d")
                metadata_header = f"""Slack Conversation Export
Channel: {conversation_name}
Channel ID: [Browser Export - No ID]
Export Date: {export_date}
Date: {date_display}
Total Messages: {len(daily_messages)}

{'='*80}

"""
                processed_messages = metadata_header + processed_messages

                # Create filename - same convention as main export
                safe_conversation_name = sanitize_filename(conversation_name)
                output_filename = f"{safe_conversation_name}_history_{date_key}.txt"
                output_filepath = os.path.join(output_dir, output_filename)

                # Write file
                try:
                    with open(output_filepath, "w", encoding="utf-8") as f:
                        f.write(processed_messages)
                        f.flush()
                        os.fsync(f.fileno())
                    
                    stats["processed"] += 1
                    stats["total_messages"] += len(daily_messages)
                    logger.info(f"Saved processed history to {output_filepath}")
                except IOError as e:
                    logger.error(f"Failed to write file {output_filepath}: {e}")
                    continue
                except Exception as e:
                    logger.error(f"Unexpected error writing file {output_filepath}: {e}", exc_info=True)
                    continue

            logger.info(f"Export complete: {stats['total_messages']} messages across {len(daily_groups)} dates")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Export Slack conversations and upload to Google Drive."
    )
    parser.add_argument(
        "--make-ref-files",
        action="store_true",
        help="Generate reference files (channels.json, people.json).",
    )
    parser.add_argument(
        "--export-history", action="store_true", help="Export conversation history."
    )
    parser.add_argument(
        "--upload-to-drive", action="store_true", help="Upload exported files to Google Drive."
    )
    parser.add_argument(
        "--setup-drive-auth",
        action="store_true",
        help="Set up Google Drive authentication and create token file for CI/CD. Run this once locally before using in CI/CD.",
    )
    parser.add_argument("--start-date", help="Start date for history export (YYYY-MM-DD).")
    parser.add_argument("--end-date", help="End date for history export (YYYY-MM-DD).")
    parser.add_argument(
        "--bulk-export",
        action="store_true",
        help="Enable bulk export mode: overrides limits and automatically chunks large exports into monthly files.",
    )
    parser.add_argument(
        "--browser-export-dm",
        action="store_true",
        help="Export DM using browser-based scraping (requires chrome-devtools MCP and pre-positioned browser).",
    )
    parser.add_argument(
        "--browser-response-dir",
        type=str,
        default="browser_exports",
        help="Directory containing DOM extraction file for browser export (default: browser_exports).",
    )
    parser.add_argument(
        "--browser-output-dir",
        type=str,
        default="slack_exports",
        help="Directory to write browser export files (default: slack_exports).",
    )
    parser.add_argument(
        "--browser-conversation-name",
        type=str,
        default="DM",
        help="Name of the conversation for browser export filename (REQUIRED: must specify actual conversation name, e.g., 'Tara').",
    )
    parser.add_argument(
        "--browser-conversation-id",
        type=str,
        help="Optional conversation ID for browser export metadata.",
    )
    parser.add_argument(
        "--browser-export-config",
        type=str,
        required=False,  # Will be checked in code for browser-export-dm
        help=f"Path to {BROWSER_EXPORT_CONFIG_FILENAME} config file (REQUIRED for --browser-export-dm).",
    )
    parser.add_argument(
        "--select-conversation",
        action="store_true",
        help="Select conversation from sidebar before extraction (default: True). Requires browser to be open.",
    )
    parser.add_argument(
        "--no-select-conversation",
        dest="select_conversation",
        action="store_false",
        help="Disable automatic conversation selection from sidebar. Use this if you've already navigated to the conversation manually.",
    )
    parser.add_argument(
        "--extract-active-threads",
        action="store_true",
        help="[Browser Export Only] Extract full history of threads with recent activity (today/yesterday). Requires --browser-export-dm.",
    )
    parser.add_argument(
        "--extract-historical-threads",
        action="store_true",
        help="[Browser Export Only] Extract historical threads via Search (in:#channel is:thread). Requires --browser-export-dm.",
    )
    parser.add_argument(
        "--search-query",
        type=str,
        help="Custom search query for historical thread extraction (e.g., 'in:#proj-foo after:2024-01-01'). If not provided, one is constructed from args.",
    )
    # Set default to True after adding both arguments
    parser.set_defaults(select_conversation=True)

    args = parser.parse_args()

    if args.setup_drive_auth:
        # Handle setup-drive-auth separately - doesn't require other args
        google_drive_credentials_file = os.getenv("GOOGLE_DRIVE_CREDENTIALS_FILE", "").strip()
        if not google_drive_credentials_file:
            logger.error(
                "GOOGLE_DRIVE_CREDENTIALS_FILE environment variable is required for --setup-drive-auth. Exiting."
            )
            sys.exit(1)

        try:
            google_drive_credentials_file = os.path.abspath(
                os.path.expanduser(google_drive_credentials_file)
            )
            if not os.path.exists(google_drive_credentials_file):
                logger.error(
                    f"Credentials file not found: {sanitize_path_for_logging(google_drive_credentials_file)}"
                )
                sys.exit(1)
            if not os.path.isfile(google_drive_credentials_file):
                logger.error(
                    f"Credentials path is not a file: {sanitize_path_for_logging(google_drive_credentials_file)}"
                )
                sys.exit(1)
            if not os.access(google_drive_credentials_file, os.R_OK):
                logger.error(
                    f"Credentials file is not readable: {sanitize_path_for_logging(google_drive_credentials_file)}"
                )
                sys.exit(1)
        except (OSError, ValueError) as e:
            logger.error(f"Invalid credentials file path: {sanitize_path_for_logging(str(e))}")
            sys.exit(1)

        try:
            token_path = GoogleDriveClient.setup_authentication(google_drive_credentials_file)
            logger.info("=" * 80)
            logger.info("Google Drive authentication setup complete!")
            logger.info(f"Token file created at: {token_path}")
            logger.info("")
            logger.info("Next steps for CI/CD:")
            logger.info("1. Copy the contents of the token file")
            logger.info("2. Add it as a CI/CD variable (file type) in your GitLab project")
            logger.info("3. Set GOOGLE_DRIVE_TOKEN_FILE in your CI/CD to point to that variable")
            logger.info("4. Add 'chmod 600 \"${GOOGLE_DRIVE_TOKEN_FILE}\"' to your CI/CD script")
            logger.info("=" * 80)
        except Exception as e:
            logger.error(f"Failed to set up authentication: {e}", exc_info=True)
            sys.exit(1)
    elif args.browser_export_dm:
        # This block is handled inside main() function - call main() to execute it
        main(args, mcp_evaluate_script=None, mcp_click=None, mcp_press_key=None, mcp_fill=None)
    elif not any([args.make_ref_files, args.export_history, args.upload_to_drive]):
        parser.print_help()
    else:
        main(args, mcp_evaluate_script=None, mcp_click=None, mcp_press_key=None, mcp_fill=None)
