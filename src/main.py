import argparse
import os
import re
import sys
import time
from calendar import monthrange
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

# Add project root to Python path so imports work regardless of how script is invoked
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from dotenv import load_dotenv
from slack_sdk.errors import SlackApiError

from src.google_drive import GoogleDriveClient
from src.slack_client import SHARE_RATE_LIMIT_DELAY, SHARE_RATE_LIMIT_INTERVAL, SlackClient
from src.utils import (
    convert_date_to_timestamp,
    create_directory,
    format_timestamp,
    load_json_file,
    sanitize_filename,
    sanitize_folder_name,
    sanitize_path_for_logging,
    save_json_file,
    setup_logging,
    validate_channel_id,
    validate_channels_json,
    validate_email,
    validate_people_json,
)

# Load environment variables from .env file if it exists
load_dotenv()

logger = setup_logging()

# Constants
CONVERSATION_DELAY_SECONDS = 0.5
LARGE_CONVERSATION_THRESHOLD = 10000
SECONDS_PER_DAY = 86400  # Seconds in a day
BYTES_PER_MB = 1024 * 1024  # Bytes per megabyte


# Environment variable parsing with validation
def _get_env_int(key: str, default: int) -> int:
    """Safely parse integer environment variable with fallback."""
    try:
        value = os.getenv(key)
        if value is None:
            return default
        return int(value)
    except ValueError:
        logger.warning(f"Invalid {key} value '{os.getenv(key)}', using default: {default}")
        return default


MAX_FILE_SIZE_MB = _get_env_int("MAX_EXPORT_FILE_SIZE_MB", 100)
MAX_MESSAGES_PER_CONVERSATION = _get_env_int("MAX_MESSAGES_PER_CONVERSATION", 50000)
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * BYTES_PER_MB
# Maximum date range in days (1 year)
MAX_DATE_RANGE_DAYS = _get_env_int("MAX_DATE_RANGE_DAYS", 365)
# Chunking thresholds for bulk exports
CHUNK_DATE_RANGE_DAYS = 30  # Chunk if date range exceeds this
CHUNK_MESSAGE_THRESHOLD = 10000  # Chunk if message count exceeds this


def replace_user_ids_in_text(
    text: str,
    slack_client: SlackClient,
    people_cache: Optional[Dict[str, str]] = None,
) -> str:
    """Replace user IDs in message text with user display names.

    Handles Slack user mention formats:
    - <@U1234567890> (standard mention format)
    - @U1234567890 (mention without angle brackets)

    Args:
        text: Message text that may contain user IDs
        slack_client: SlackClient instance for looking up user info
        people_cache: Optional cache dictionary mapping user IDs to display names

    Returns:
        Text with user IDs replaced by display names
    """
    if not text:
        return text

    # Pattern to match Slack user mentions: <@U...> or @U...
    # User IDs start with U and are followed by alphanumeric characters
    pattern = r"<@(U[A-Z0-9]+)>|@(U[A-Z0-9]+)"

    def replace_match(match: re.Match) -> str:
        # Extract user ID from either capture group
        user_id = match.group(1) or match.group(2)
        if not user_id:
            return match.group(0)  # Return original if no match

        # Check cache first
        if people_cache and user_id in people_cache:
            display_name = people_cache[user_id]
        else:
            # Look up user info
            user_info = slack_client.get_user_info(user_id)
            if user_info:
                display_name = user_info.get("displayName", user_id)
                # Update cache for future use
                if people_cache is not None:
                    people_cache[user_id] = display_name
            else:
                # If user lookup fails, keep the original ID
                display_name = user_id

        # Replace with @DisplayName format to preserve mention context
        return f"@{display_name}"

    # Replace all matches
    return re.sub(pattern, replace_match, text)


def group_messages_by_date(
    history: List[Dict[str, Any]],
) -> Dict[str, List[Dict[str, Any]]]:
    """Group messages by date (YYYYMMDD format).

    Args:
        history: List of messages with 'ts' timestamps

    Returns:
        Dictionary mapping date strings (YYYYMMDD) to lists of messages
    """
    daily_groups: Dict[str, List[Dict[str, Any]]] = {}

    for message in history:
        ts_str = message.get("ts")
        if not ts_str:
            continue

        try:
            ts = float(ts_str)
            if ts <= 0:
                continue
        except (ValueError, TypeError):
            continue

        msg_date = datetime.fromtimestamp(ts, tz=timezone.utc)
        date_key = msg_date.strftime("%Y%m%d")

        if date_key not in daily_groups:
            daily_groups[date_key] = []

        daily_groups[date_key].append(message)

    # Sort messages within each day by timestamp
    for date_key in daily_groups:
        daily_groups[date_key].sort(key=lambda x: float(x.get("ts", 0)))

    return daily_groups


def preprocess_history(
    history_data: List[Dict[str, Any]],
    slack_client: SlackClient,
    people_cache: Optional[Dict[str, str]] = None,
    use_display_names: bool = False,
) -> str:
    """Processes Slack history into a human-readable format.
    
    Args:
        history_data: List of message dictionaries
        slack_client: SlackClient instance for looking up user info (can be None if use_display_names=True)
        people_cache: Optional cache dictionary mapping user IDs to display names
        use_display_names: If True, treat 'user' field as display name directly (for browser exports)
                          If False, treat 'user' field as user ID and look up display name (API exports)
    """
    threads = {}
    for message in history_data:
        text = message.get("text", "")
        files = message.get("files")

        # If no text and no files, skip
        if not text and not files:
            continue

        # If no text but has files, use a placeholder
        if not text and files:
            text = "[File attached]"
        # If text and files, append placeholder
        elif text and files:
            text += " [File attached]"

        # Replace user IDs in message text with user names (only if not using display names)
        if not use_display_names:
            text = replace_user_ids_in_text(text, slack_client, people_cache)

        thread_key = message.get("thread_ts", message.get("ts"))
        if not thread_key:
            continue

        if thread_key not in threads:
            threads[thread_key] = []

        ts = message.get("ts")

        user_id = message.get("user")
        name = "Unknown User"
        if user_id:
            if use_display_names:
                # For browser exports, user_id is already a display name
                name = user_id
            else:
                # For API exports, user_id is a Slack user ID (U...)
                # Check cache first
                if people_cache and user_id in people_cache:
                    name = people_cache[user_id]
                else:
                    if slack_client:
                        user_info = slack_client.get_user_info(user_id)
                        if user_info:
                            name = user_info.get("displayName", message.get("username", user_id))
                            # Update cache for future use
                            if people_cache is not None:
                                people_cache[user_id] = name
                    else:
                        # No slack_client available, use user_id as fallback
                        name = user_id

        text = text.replace("\n", "\n    ")

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

        for reply_ts, reply_name, reply_text in messages_in_thread[1:]:
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
        # If members not in channel_info, fetch them dynamically
        if not members:
            logger.debug(
                f"Group DM {channel_id} has no members in channel_info, fetching dynamically"
            )
            members = slack_client.get_channel_members(channel_id)
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


def should_chunk_export(
    history: List[Dict[str, Any]],
    oldest_ts: Optional[str],
    latest_ts: Optional[str],
    bulk_export: bool,
) -> bool:
    """Determine if export should be chunked based on thresholds.

    Args:
        history: List of messages
        oldest_ts: Oldest timestamp (Unix timestamp string)
        latest_ts: Latest timestamp (Unix timestamp string)
        bulk_export: Whether bulk export mode is enabled

    Returns:
        True if export should be chunked, False otherwise
    """
    if not bulk_export:
        return False

    if not history:
        return False

    # Check message count threshold
    if len(history) > CHUNK_MESSAGE_THRESHOLD:
        return True

    # Check date range threshold - calculate from messages if timestamps not provided
    if oldest_ts and latest_ts:
        date_range_days = (float(latest_ts) - float(oldest_ts)) / SECONDS_PER_DAY
        if date_range_days > CHUNK_DATE_RANGE_DAYS:
            return True
    elif len(history) > 1:
        # Calculate date range from messages themselves - use generator for efficiency
        timestamps_gen = (float(msg.get("ts", 0)) for msg in history if msg.get("ts"))
        timestamps_list = list(timestamps_gen)
        if timestamps_list:
            min_ts = min(timestamps_list)
            max_ts = max(timestamps_list)
            date_range_days = (max_ts - min_ts) / SECONDS_PER_DAY
            if date_range_days > CHUNK_DATE_RANGE_DAYS:
                return True

    return False


def split_messages_by_month(
    history: List[Dict[str, Any]],
) -> List[Tuple[datetime, datetime, List[Dict[str, Any]]]]:
    """Split messages into monthly chunks.

    Args:
        history: List of messages sorted by timestamp

    Returns:
        List of tuples: (start_date, end_date, messages_for_month)
    """
    if not history:
        return []

    chunks = []
    current_month_start = None
    current_chunk = []

    for message in history:
        # Validate timestamp before conversion
        ts_str = message.get("ts")
        if not ts_str:
            logger.warning(f"Message missing timestamp, skipping: {message.get('text', '')[:50]}")
            continue
        try:
            ts = float(ts_str)
            if ts <= 0:
                logger.warning(f"Invalid timestamp value {ts}, skipping message")
                continue
        except (ValueError, TypeError) as e:
            logger.warning(f"Invalid timestamp format '{ts_str}': {e}, skipping message")
            continue

        msg_date = datetime.fromtimestamp(ts, tz=timezone.utc)

        # Determine month boundaries
        month_start = datetime(msg_date.year, msg_date.month, 1, tzinfo=timezone.utc)

        if current_month_start is None or month_start != current_month_start:
            # Save previous chunk if it exists
            if current_chunk:
                # Calculate end of previous month
                last_msg = current_chunk[-1]
                last_ts_str = last_msg.get("ts")
                if not last_ts_str:
                    logger.warning("Last message in chunk missing timestamp, using current time")
                    last_msg_date = datetime.now(timezone.utc)
                else:
                    try:
                        last_msg_ts = float(last_ts_str)
                        last_msg_date = datetime.fromtimestamp(last_msg_ts, tz=timezone.utc)
                    except (ValueError, TypeError):
                        logger.warning(f"Invalid timestamp in last message, using current time")
                        last_msg_date = datetime.now(timezone.utc)
                days_in_month = monthrange(last_msg_date.year, last_msg_date.month)[1]
                month_end = datetime(
                    last_msg_date.year,
                    last_msg_date.month,
                    days_in_month,
                    23,
                    59,
                    59,
                    tzinfo=timezone.utc,
                )
                chunks.append((current_month_start, month_end, current_chunk))

            # Start new chunk
            current_month_start = month_start
            current_chunk = []

        current_chunk.append(message)

    # Add final chunk
    if current_chunk:
        last_msg = current_chunk[-1]
        last_ts_str = last_msg.get("ts")
        if not last_ts_str:
            logger.warning("Last message in final chunk missing timestamp, using current time")
            last_msg_date = datetime.now(timezone.utc)
        else:
            try:
                last_msg_ts = float(last_ts_str)
                last_msg_date = datetime.fromtimestamp(last_msg_ts, tz=timezone.utc)
            except (ValueError, TypeError):
                logger.warning(f"Invalid timestamp in last message, using current time")
                last_msg_date = datetime.now(timezone.utc)
        days_in_month = monthrange(last_msg_date.year, last_msg_date.month)[1]
        month_end = datetime(
            last_msg_date.year, last_msg_date.month, days_in_month, 23, 59, 59, tzinfo=timezone.utc
        )
        chunks.append((current_month_start, month_end, current_chunk))

    return chunks


def estimate_file_size(processed_history: str) -> int:
    """Estimate file size in bytes.

    Args:
        processed_history: Processed history text

    Returns:
        Estimated size in bytes
    """
    return len(processed_history.encode("utf-8"))


def _should_share_with_member(
    member_id: str,
    user_info: Optional[Dict[str, str]],
    share_members: Optional[List[str]],
) -> bool:
    """Check if a member should be shared with based on shareMembers list.

    Args:
        member_id: Slack user ID
        user_info: User info dictionary with slackId, email, displayName
        share_members: Optional list of identifiers (user IDs, emails, or display names)

    Returns:
        True if member should be shared with, False otherwise
    """
    if not share_members or len(share_members) == 0:
        # No shareMembers list or empty list = share with all (backward compatible)
        return True

    if not user_info:
        return False

    # Normalize identifiers for comparison
    user_slack_id = user_info.get("slackId", "").lower()
    user_email = user_info.get("email", "").lower()
    user_display_name = user_info.get("displayName", "").strip().lower()

    # Check each identifier in shareMembers
    for identifier in share_members:
        if not identifier:
            continue

        identifier_lower = identifier.strip().lower()

        # Match by Slack user ID
        if identifier_lower == user_slack_id:
            return True

        # Match by email
        if user_email and identifier_lower == user_email:
            return True

        # Match by display name (case-insensitive)
        if user_display_name and identifier_lower == user_display_name:
            return True

    # Not found in shareMembers list
    return False


def share_folder_with_members(
    google_drive_client: GoogleDriveClient,
    folder_id: str,
    slack_client: SlackClient,
    channel_id: str,
    channel_name: str,
    channel_info: Dict[str, Any],
    no_notifications_set: set,
    no_share_set: set,
    stats: Dict[str, int],
    sanitized_folder_name: Optional[str] = None,
) -> None:
    """Share a Google Drive folder with channel members and manage permissions.

    Args:
        google_drive_client: GoogleDriveClient instance
        folder_id: Google Drive folder ID
        slack_client: SlackClient instance
        channel_id: Slack channel ID
        channel_name: Display name of the channel
        channel_info: Channel configuration dictionary
            - share: bool - whether to share (default: True)
            - shareMembers: Optional[List[str]] - list of user IDs, emails, or display names to share with
        no_notifications_set: Set of emails who opted out of notifications
        no_share_set: Set of emails who opted out of being shared with
        stats: Statistics dictionary to update
    """
    # Check if sharing is enabled
    should_share = channel_info.get("share", True)
    if not should_share:
        logger.info(f"Sharing disabled for {channel_name} (share: false in channels.json)")
        return

    members = slack_client.get_channel_members(channel_id)
    if not members:
        logger.warning(f"No members found for {channel_name}. Skipping sharing.")
        return

    # Get shareMembers list if provided
    share_members = channel_info.get("shareMembers")
    # Validate shareMembers is a list if provided
    if share_members is not None:
        if not isinstance(share_members, list):
            logger.warning(
                f"shareMembers must be a list for {channel_name}, got {type(share_members).__name__}. Ignoring."
            )
            share_members = None
        elif len(share_members) == 0:
            # Empty list = share with all (backward compatible)
            share_members = None
    if share_members:
        logger.info(
            f"Selective sharing enabled for {channel_name}: sharing with {len(share_members)} specified member(s)"
        )

    # Get current folder permissions to identify who should have access removed
    current_permissions = google_drive_client.get_folder_permissions(folder_id)
    current_member_emails = set()

    # Build set of current member emails (only those who should have access)
    for member_id in members:
        user_info = slack_client.get_user_info(member_id)
        if user_info and user_info.get("email"):
            email = user_info["email"]
            if validate_email(email):
                # Check if member should be shared with (respects shareMembers and no_share_set)
                if email.lower() not in no_share_set:
                    if _should_share_with_member(member_id, user_info, share_members):
                        current_member_emails.add(email.lower())

    # Revoke access for people who are no longer members
    revoked_count = 0
    revoke_errors = []
    for perm in current_permissions:
        # Only revoke user permissions (not owner, domain, etc.)
        if perm.get("type") != "user":
            continue

        # Don't revoke owner permissions
        if perm.get("role") == "owner":
            continue

        perm_email = perm.get("emailAddress", "").lower()
        if not perm_email:
            continue

        # If this email is not in current members, revoke access
        if perm_email not in current_member_emails:
            try:
                # Rate limit revoke operations
                if revoked_count > 0 and revoked_count % SHARE_RATE_LIMIT_INTERVAL == 0:
                    time.sleep(SHARE_RATE_LIMIT_DELAY)

                revoked = google_drive_client.revoke_folder_access(folder_id, perm_email)
                if revoked:
                    revoked_count += 1
                else:
                    revoke_errors.append(f"{perm_email}: revoke failed")
            except Exception as e:
                revoke_errors.append(f"{perm_email}: {str(e)}")

    if revoked_count > 0:
        logger.info(f"Revoked access for {revoked_count} user(s) no longer in {channel_name}")
    if revoke_errors:
        logger.warning(f"Failed to revoke access for some users: {', '.join(revoke_errors)}")

    # Share with current members
    shared_emails = set()
    share_errors = []
    share_failures = 0
    excluded_count = 0
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

            # Skip if user has opted out of being shared with
            if email.lower() in no_share_set:
                logger.debug(f"User {email} has opted out of being shared with, skipping")
                excluded_count += 1
                continue

            # Check if member should be shared with based on shareMembers list
            if not _should_share_with_member(member_id, user_info, share_members):
                logger.debug(
                    f"User {email} ({user_info.get('displayName', member_id)}) not in shareMembers list, skipping"
                )
                excluded_count += 1
                continue

            if email not in shared_emails:
                try:
                    # Check if user has opted out of notifications
                    send_notification = email.lower() not in no_notifications_set
                    if not send_notification:
                        logger.debug(
                            f"User {email} has opted out of notifications, sharing without notification"
                        )

                    shared = google_drive_client.share_folder(
                        folder_id, email, send_notification=send_notification
                    )
                    if shared:
                        shared_emails.add(email)
                        stats["shared"] += 1
                    else:
                        share_errors.append(f"{email}: share failed")
                        share_failures += 1
                except Exception as e:
                    share_errors.append(f"{email}: {str(e)}")
                    share_failures += 1

    stats["share_failed"] += share_failures

    if share_errors:
        logger.warning(f"Failed to share with some users: {', '.join(share_errors)}")

    # Log summary
    if share_members:
        logger.info(
            f"Selective sharing complete: shared with {len(shared_emails)} of {len(members)} channel members"
        )
        if excluded_count > 0:
            logger.info(f"Excluded {excluded_count} member(s) not in shareMembers list")
    else:
        logger.info(f"Shared folder with {len(shared_emails)} participants")


def _validate_and_setup_environment():
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


def _setup_output_directory():
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


def load_browser_export_config(config_path: str) -> Optional[Dict[str, Any]]:
    """Load browser-export.json configuration file.
    
    Args:
        config_path: Path to browser-export.json file
        
    Returns:
        Dictionary with browser-export configuration, or None if not found/invalid
    """
    try:
        config_data = load_json_file(config_path)
        if not config_data:
            logger.debug(f"Browser export config file not found: {config_path}")
            return None
        
        browser_exports = config_data.get("browser-export", [])
        if not isinstance(browser_exports, list):
            logger.warning(f"Invalid browser-export.json structure: 'browser-export' must be a list")
            return None
        
        return {"browser-export": browser_exports}
    except Exception as e:
        logger.warning(f"Error loading browser-export config: {e}")
        return None


def find_conversation_in_config(config_data: Dict[str, Any], conversation_id: str = None, conversation_name: str = None) -> Optional[Dict[str, Any]]:
    """Find a conversation in browser-export.json by ID or name.
    
    Args:
        config_data: Browser export config dictionary
        conversation_id: Optional conversation ID to search for
        conversation_name: Optional conversation name to search for
        
    Returns:
        Conversation info dictionary, or None if not found
    """
    if not config_data:
        return None
    
    browser_exports = config_data.get("browser-export", [])
    if not browser_exports:
        return None
    
    for conv in browser_exports:
        if conversation_id and conv.get("id") == conversation_id:
            return conv
        if conversation_name and conv.get("name") == conversation_name:
            return conv
    
    return None


def select_conversation_from_sidebar(conversation_id: str) -> bool:
    """Select a conversation from the Slack sidebar by clicking on it.
    
    This function uses MCP chrome-devtools tools to find and click on the conversation
    in the sidebar. The conversation is identified by its div ID.
    
    Args:
        conversation_id: Slack conversation ID (e.g., "D06DDJ2UH2M")
        
    Returns:
        True if conversation was successfully selected, False otherwise
        
    Note:
        This function requires MCP chrome-devtools tools to be available.
        It should be called before extracting messages.
    """
    # This function will be called by the agent using MCP tools
    # We can't call MCP tools directly here, so we'll document the approach
    # and the agent will implement it using mcp_chrome-devtools tools
    
    logger.info(f"To select conversation {conversation_id} from sidebar:")
    logger.info("1. Take a snapshot of the page")
    logger.info("2. Find the div with id matching conversation_id")
    logger.info("3. Find the parent treeitem element")
    logger.info("4. Click on the treeitem or its button/link child")
    logger.info("5. Wait for the conversation to load")
    
    # The actual implementation will be done by the agent using MCP tools
    return True


def share_folder_for_browser_export(
    google_drive_client: GoogleDriveClient,
    folder_id: str,
    slack_client: SlackClient,
    conversation_info: Dict[str, Any],
    conversation_name: str,
    no_notifications_set: set,
    no_share_set: set,
    stats: Dict[str, int],
) -> None:
    """Share a Google Drive folder for browser export using the same logic as Slack export.
    
    Args:
        google_drive_client: GoogleDriveClient instance
        folder_id: Google Drive folder ID
        slack_client: SlackClient instance (required for member lookup)
        conversation_info: Conversation info from browser-export.json
        conversation_name: Display name of the conversation
        no_notifications_set: Set of emails who opted out of notifications
        no_share_set: Set of emails who opted out of being shared with
        stats: Statistics dictionary to update
    """
    # Check if sharing is enabled
    should_share = conversation_info.get("share", True)
    if not should_share:
        logger.info(f"Sharing disabled for {conversation_name} (share: false in browser-export.json)")
        return
    
    # Get conversation ID
    conversation_id = conversation_info.get("id")
    if not conversation_id:
        logger.warning(f"No conversation ID found for {conversation_name}. Cannot share.")
        return
    
    # Get members - for browser exports, we need to use Slack API
    # For DMs (is_im), get the other user
    # For group DMs (is_mpim), get all members
    members = []
    if conversation_info.get("is_im"):
        # DM - get the other user
        other_user_id = conversation_info.get("user")
        if other_user_id:
            members = [other_user_id]
        else:
            # Try to get from conversation ID (DM IDs start with D)
            # For DMs, we need to use conversations.info to get the user
            try:
                conv_info = slack_client.client.conversations_info(channel=conversation_id)
                if conv_info.get("ok"):
                    user_id = conv_info.get("channel", {}).get("user")
                    if user_id:
                        members = [user_id]
            except Exception as e:
                logger.warning(f"Could not get user for DM {conversation_id}: {e}")
    elif conversation_info.get("is_mpim"):
        # Group DM - get all members
        members = slack_client.get_channel_members(conversation_id)
    
    if not members:
        logger.warning(f"No members found for {conversation_name}. Skipping sharing.")
        return
    
    # Get shareMembers list if provided
    share_members = conversation_info.get("shareMembers")
    # Validate shareMembers is a list if provided
    if share_members is not None:
        if not isinstance(share_members, list):
            logger.warning(
                f"shareMembers must be a list for {conversation_name}, got {type(share_members).__name__}. Ignoring."
            )
            share_members = None
        elif len(share_members) == 0:
            # Empty list = share with all (backward compatible)
            share_members = None
    if share_members:
        logger.info(
            f"Selective sharing enabled for {conversation_name}: sharing with {len(share_members)} specified member(s)"
        )
    
    # Get current folder permissions to identify who should have access removed
    current_permissions = google_drive_client.get_folder_permissions(folder_id)
    current_member_emails = set()
    
    # Build set of current member emails (only those who should have access)
    for member_id in members:
        user_info = slack_client.get_user_info(member_id)
        if user_info and user_info.get("email"):
            email = user_info["email"]
            if validate_email(email):
                # Check if member should be shared with (respects shareMembers and no_share_set)
                if email.lower() not in no_share_set:
                    if _should_share_with_member(member_id, user_info, share_members):
                        current_member_emails.add(email.lower())
    
    # Revoke access for people who are no longer members
    revoked_count = 0
    revoke_errors = []
    for perm in current_permissions:
        # Only revoke user permissions (not owner, domain, etc.)
        if perm.get("type") != "user":
            continue
        
        # Don't revoke owner permissions
        if perm.get("role") == "owner":
            continue
        
        perm_email = perm.get("emailAddress", "").lower()
        if not perm_email:
            continue
        
        # If this email is not in current members, revoke access
        if perm_email not in current_member_emails:
            try:
                # Rate limit revoke operations
                if revoked_count > 0 and revoked_count % SHARE_RATE_LIMIT_INTERVAL == 0:
                    time.sleep(SHARE_RATE_LIMIT_DELAY)
                
                revoked = google_drive_client.revoke_folder_access(folder_id, perm_email)
                if revoked:
                    revoked_count += 1
                else:
                    revoke_errors.append(f"{perm_email}: revoke failed")
            except Exception as e:
                revoke_errors.append(f"{perm_email}: {str(e)}")
    
    if revoked_count > 0:
        logger.info(f"Revoked access for {revoked_count} user(s) no longer in {conversation_name}")
    if revoke_errors:
        logger.warning(f"Failed to revoke access for some users: {', '.join(revoke_errors)}")
    
    # Share with current members
    shared_emails = set()
    share_errors = []
    share_failures = 0
    excluded_count = 0
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
            
            # Skip if user has opted out of being shared with
            if email.lower() in no_share_set:
                logger.debug(f"User {email} has opted out of being shared with, skipping")
                excluded_count += 1
                continue
            
            # Check if member should be shared with based on shareMembers list
            if not _should_share_with_member(member_id, user_info, share_members):
                logger.debug(
                    f"User {email} ({user_info.get('displayName', member_id)}) not in shareMembers list, skipping"
                )
                excluded_count += 1
                continue
            
            if email not in shared_emails:
                try:
                    # Check if user has opted out of notifications
                    send_notification = email.lower() not in no_notifications_set
                    if not send_notification:
                        logger.debug(
                            f"User {email} has opted out of notifications, sharing without notification"
                        )
                    
                    shared = google_drive_client.share_folder(
                        folder_id, email, send_notification=send_notification
                    )
                    if shared:
                        shared_emails.add(email)
                        stats["shared"] += 1
                    else:
                        share_errors.append(f"{email}: share failed")
                        share_failures += 1
                except Exception as e:
                    share_errors.append(f"{email}: {str(e)}")
                    share_failures += 1
    
    stats["share_failed"] += share_failures
    
    if share_errors:
        logger.warning(f"Failed to share with some users: {', '.join(share_errors)}")
    
    # Log summary
    if share_members:
        logger.info(
            f"Selective sharing complete: shared with {len(shared_emails)} of {len(members)} conversation members"
        )
        if excluded_count > 0:
            logger.info(f"Excluded {excluded_count} member(s) not in shareMembers list")
    else:
        logger.info(f"Shared folder with {len(shared_emails)} participants")


def _load_people_cache():
    """Load people.json cache and opt-out sets.

    Returns:
        Tuple of (people_cache dict, no_notifications_set, no_share_set)
    """
    people_cache = {}
    no_notifications_set = set()  # Set of emails who have opted out of notifications
    no_share_set = set()  # Set of emails who have opted out of being shared with
    people_json = load_json_file("config/people.json")
    if people_json:
        # Validate people.json structure
        try:
            validate_people_json(people_json)
        except ValueError as e:
            logger.warning(
                f"Invalid people.json structure: {e}. Will lookup users on-demand from Slack API."
            )
            people_cache = {}
        else:
            people_cache = {p["slackId"]: p["displayName"] for p in people_json.get("people", [])}
            # Build sets of opt-out preferences
            for p in people_json.get("people", []):
                if p.get("email"):
                    email_lower = p["email"].lower()
                    if p.get("noNotifications") is True:
                        no_notifications_set.add(email_lower)
                    if p.get("noShare") is True:
                        no_share_set.add(email_lower)
            logger.info(f"Loaded {len(people_cache)} users from people.json cache")
            if no_notifications_set:
                logger.info(
                    f"Found {len(no_notifications_set)} user(s) who have opted out of notifications"
                )
            if no_share_set:
                logger.info(
                    f"Found {len(no_share_set)} user(s) who have opted out of being shared with"
                )
    else:
        logger.info("No people.json found - will lookup users on-demand from Slack API")
    return people_cache, no_notifications_set, no_share_set


def get_oldest_timestamp_for_export(
    google_drive_client: Optional[GoogleDriveClient],
    folder_id: Optional[str],
    conversation_name: str,
    explicit_start_date: Optional[str],
    upload_to_drive: bool,
    sanitized_folder_name: Optional[str] = None,
    safe_conversation_name: Optional[str] = None,
) -> Optional[str]:
    """Get oldest timestamp for incremental export.

    Determines the oldest timestamp to use for fetching messages, considering:
    1. Explicit --start-date if provided
    2. Last export timestamp from Google Drive (if uploading to Drive)
    3. Uses the later of the two to avoid missing messages

    Args:
        google_drive_client: Optional GoogleDriveClient instance (None if not uploading to Drive)
        folder_id: Optional folder ID (may be None if folder not yet created)
        conversation_name: Display name of the conversation
        explicit_start_date: Optional explicit start date string (from --start-date)
        upload_to_drive: Whether uploading to Drive (determines if we check Drive metadata)
        sanitized_folder_name: Optional sanitized folder name (for creating folder if needed)
        safe_conversation_name: Optional safe conversation name (for metadata lookup)

    Returns:
        Oldest timestamp string, or None if no limit (fetch all messages)
    """
    oldest_ts = None
    explicit_start_ts = None

    # Parse explicit start date if provided
    if explicit_start_date:
        explicit_start_ts = convert_date_to_timestamp(explicit_start_date)
        if explicit_start_ts is None:
            logger.error(f"Invalid start date format: {explicit_start_date}")
            return None
        logger.info(f"Explicit start date provided: {explicit_start_date} ({explicit_start_ts})")

    # Check Google Drive for last export timestamp if uploading to Drive
    if upload_to_drive and google_drive_client:
        # Create or get folder if we don't have folder_id yet
        if not folder_id and sanitized_folder_name:
            folder_id = google_drive_client.create_folder(
                sanitized_folder_name, None  # Will use default parent folder
            )

        if folder_id:
            # Use safe_conversation_name if provided, otherwise sanitize conversation_name
            if not safe_conversation_name:
                safe_conversation_name = sanitize_filename(conversation_name)

            last_export_ts = google_drive_client.get_latest_export_timestamp(
                folder_id, safe_conversation_name
            )

            if last_export_ts:
                # Use the later of explicit start date or last export timestamp
                if explicit_start_ts:
                    oldest_ts = max(explicit_start_ts, last_export_ts)
                    if oldest_ts == last_export_ts:
                        last_export_dt = datetime.fromtimestamp(
                            float(last_export_ts), tz=timezone.utc
                        )
                        logger.info(
                            f"Last export ({last_export_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}) "
                            f"is later than explicit start date. Using last export timestamp."
                        )
                    else:
                        logger.info(
                            f"Explicit start date is later than last export. Using explicit start date."
                        )
                else:
                    oldest_ts = last_export_ts
                    last_export_dt = datetime.fromtimestamp(
                        float(last_export_ts), tz=timezone.utc
                    )
                    logger.info(
                        f"Fetching messages since last export: {last_export_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}"
                    )
            else:
                # No previous export found - use explicit start date if provided
                oldest_ts = explicit_start_ts
                if oldest_ts:
                    logger.info("No previous export found in Drive, using explicit start date")
                else:
                    logger.info("No previous export found in Drive, processing all messages")
        else:
            # Could not access/create folder - use explicit start date if provided
            oldest_ts = explicit_start_ts
            if oldest_ts:
                logger.info("Could not access/create folder, using explicit start date")
            else:
                logger.info("Could not access/create folder, processing all messages")
    else:
        # Not uploading to Drive - use explicit start date if provided
        oldest_ts = explicit_start_ts
        if oldest_ts:
            logger.info(f"Not uploading to Drive, using explicit start date: {explicit_start_date}")
        else:
            logger.info(
                "Not uploading to Drive, processing all messages (use --start-date for incremental export)"
            )

    return oldest_ts


def upload_messages_to_drive(
    messages: List[Dict[str, Any]],
    conversation_name: str,
    conversation_id: Optional[str],
    google_drive_client: GoogleDriveClient,
    google_drive_folder_id: Optional[str],
    slack_client: Optional[SlackClient],
    people_cache: Optional[Dict[str, str]],
    use_display_names: bool = False,
    stats: Optional[Dict[str, int]] = None,
) -> Dict[str, int]:
    """Upload messages to Google Drive, grouped by date.

    Unified function for uploading messages to Google Drive from both API and browser exports.
    Creates daily Google Docs with proper metadata headers and handles incremental updates.

    Args:
        messages: List of message dictionaries to upload
        conversation_name: Display name of the conversation
        conversation_id: Slack conversation ID (None for browser exports)
        google_drive_client: GoogleDriveClient instance
        google_drive_folder_id: Parent folder ID in Google Drive
        slack_client: Optional SlackClient for user lookups (None for browser exports)
        people_cache: Optional cache of user info (None for browser exports)
        use_display_names: If True, use display names from messages instead of looking up via API
        stats: Optional statistics dictionary to update (creates new one if None)

    Returns:
        Statistics dictionary with upload results
    """
    if stats is None:
        stats = {
            "processed": 0,
            "uploaded": 0,
            "upload_failed": 0,
            "total_messages": 0,
        }

    # Group messages by date
    daily_groups = group_messages_by_date(messages)
    logger.info(
        f"Grouped {len(messages)} messages into {len(daily_groups)} daily group(s)"
    )

    if not daily_groups:
        logger.warning("No messages found to upload")
        return stats

    # Create or get folder
    sanitized_folder_name = sanitize_folder_name(conversation_name)
    folder_id = google_drive_client.create_folder(
        sanitized_folder_name, google_drive_folder_id
    )

    if not folder_id:
        logger.error(f"Failed to create/get folder for {conversation_name}")
        return stats

    logger.info(f"Using folder: {sanitized_folder_name} ({folder_id})")

    # Sort dates chronologically
    sorted_dates = sorted(daily_groups.keys())

    for date_key in sorted_dates:
        daily_messages = daily_groups[date_key]
        logger.info(f"Processing {len(daily_messages)} messages for date {date_key}")

        # Process messages for this day
        if use_display_names:
            processed_messages = preprocess_history(
                daily_messages, slack_client=None, people_cache=None, use_display_names=True
            )
        else:
            processed_messages = preprocess_history(
                daily_messages, slack_client, people_cache
            )

        # Check for empty history after processing
        if not processed_messages or not processed_messages.strip():
            logger.warning(
                f"No processable content found for {date_key} of {conversation_name}. Skipping."
            )
            continue

        # Create doc name: conversation name slack messages yyyymmdd
        doc_name_base = f"{conversation_name} slack messages {date_key}"
        doc_name = sanitize_folder_name(doc_name_base)

        # Check if doc already exists to determine if we need a header
        escaped_doc_name = google_drive_client._escape_drive_query_string(doc_name)
        escaped_folder_id = google_drive_client._escape_drive_query_string(folder_id)
        query = (
            f"name='{escaped_doc_name}' and '{escaped_folder_id}' in parents "
            f"and mimeType='application/vnd.google-apps.document' and trashed=false"
        )

        doc_exists = False
        try:
            google_drive_client._rate_limit()
            results = (
                google_drive_client.service.files()
                .list(q=query, fields="files(id, name, modifiedTime)", pageSize=100)
                .execute()
            )
            existing_files = results.get("files", [])
            if existing_files:
                doc_exists = True
                if len(existing_files) > 1:
                    logger.warning(
                        f"Found {len(existing_files)} documents with name '{doc_name}'. "
                        f"create_or_update_google_doc() will use the most recently modified."
                    )
        except Exception as e:
            logger.debug(
                f"Error checking for existing doc '{doc_name}': {e}, assuming new doc"
            )

        # Prepare content: add header only for new docs
        if doc_exists:
            # Append separator and messages (no header for existing docs)
            content_to_add = f"\n\n{'='*80}\n\n{processed_messages}"
        else:
            # Add full header for new docs
            export_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            date_obj = datetime.strptime(date_key, "%Y%m%d").replace(tzinfo=timezone.utc)
            date_display = date_obj.strftime("%Y-%m-%d")
            
            # Format channel ID for metadata header
            channel_id_display = conversation_id if conversation_id else "[Browser Export - No ID]"
            
            metadata_header = f"""Slack Conversation Export
Channel: {conversation_name}
Channel ID: {channel_id_display}
Export Date: {export_date}
Date: {date_display}
Total Messages: {len(daily_messages)}

{'='*80}

"""
            content_to_add = metadata_header + processed_messages

        # Create or update Google Doc (append mode for incremental updates)
        doc_id = google_drive_client.create_or_update_google_doc(
            doc_name, content_to_add, folder_id, overwrite=False
        )

        if not doc_id:
            logger.error(
                f"Failed to create Google Doc for {date_key} of {conversation_name}"
            )
            stats["upload_failed"] += 1
        else:
            stats["uploaded"] += 1
            stats["processed"] += 1
            stats["total_messages"] += len(daily_messages)
            logger.info(f"Created/updated Google Doc for {date_key}")

    # Save export metadata with latest timestamp from all messages
    if messages:
        latest_message_ts = max(float(msg.get("ts", 0)) for msg in messages)
        safe_conversation_name = sanitize_filename(conversation_name)
        google_drive_client.save_export_metadata(
            folder_id, safe_conversation_name, str(latest_message_ts)
        )
        logger.info(f"Saved export metadata for {conversation_name}")

    return stats


def _log_statistics(stats, upload_to_drive):
    """Log export statistics.

    Args:
        stats: Statistics dictionary
        upload_to_drive: Whether Drive upload was enabled
    """
    logger.info("=" * 80)
    logger.info("Export Statistics:")
    logger.info(f"  Processed: {stats['processed']}")
    logger.info(f"  Skipped: {stats['skipped']}")
    logger.info(f"  Failed: {stats['failed']}")
    if upload_to_drive:
        logger.info(f"  Uploaded to Drive: {stats['uploaded']}")
        logger.info(f"  Upload Failed: {stats['upload_failed']}")
        logger.info(f"  Folders shared: {stats['shared']}")
        logger.info(f"  Share Failed: {stats['share_failed']}")
    logger.info(f"  Total messages processed: {stats['total_messages']}")
    logger.info("=" * 80)


def main(args):
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
        people_cache, no_notifications_set, no_share_set = _load_people_cache()

        # Setup output directory
        output_dir = _setup_output_directory()

        # Initialize statistics tracking
        stats = {
            "processed": 0,
            "skipped": 0,
            "failed": 0,
            "uploaded": 0,
            "upload_failed": 0,
            "shared": 0,
            "share_failed": 0,
            "total_messages": 0,
        }

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

            # Validate date range logic
            if oldest_ts and latest_ts:
                if float(oldest_ts) > float(latest_ts):
                    logger.error(
                        f"Start date ({args.start_date or 'last export'}) must be before end date ({args.end_date})"
                    )
                    stats["skipped"] += 1
                    continue

                # Validate date range doesn't exceed maximum (unless bulk export)
                date_range_days = (float(latest_ts) - float(oldest_ts)) / SECONDS_PER_DAY
                if effective_max_date_range and date_range_days > effective_max_date_range:
                    logger.error(
                        f"Date range ({date_range_days:.0f} days) exceeds maximum allowed ({effective_max_date_range} days). Use --bulk-export to override."
                    )
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
        _log_statistics(stats, args.upload_to_drive)


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
        help="Path to browser-export.json config file (REQUIRED for --browser-export-dm).",
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
        # Handle browser-based DM export
        # This uses the same code path as --export-history but extracts messages directly from DOM
        from pathlib import Path
        from datetime import datetime, timezone
        from src.browser_response_processor import BrowserResponseProcessor
        from src.browser_scraper import extract_messages_from_dom

        # Require browser-export.json config file
        if not args.browser_export_config:
            logger.error(
                "ERROR: --browser-export-config is required for browser exports."
            )
            logger.error(
                "Browser exports require browser-export.json to ensure consistent naming and sharing."
            )
            logger.error(
                f"Example: --browser-export-config config/browser-export.json"
            )
            sys.exit(1)
        
        # Load browser-export.json config
        config_data = load_browser_export_config(args.browser_export_config)
        if not config_data:
            logger.error(
                f"ERROR: Failed to load browser-export.json from {args.browser_export_config}"
            )
            logger.error(
                "Ensure the file exists and has valid JSON structure with 'browser-export' array."
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
                f"ERROR: Conversation not found in browser-export.json"
            )
            if args.browser_conversation_id:
                logger.error(f"  Searched by ID: {args.browser_conversation_id}")
            if args.browser_conversation_name and args.browser_conversation_name != "DM":
                logger.error(f"  Searched by name: {args.browser_conversation_name}")
            logger.error(
                "Ensure the conversation exists in browser-export.json with matching ID or name."
            )
            sys.exit(1)
        
        # Always use conversation name and ID from config (ensures consistency)
        conversation_name = conversation_info.get("name")
        if not conversation_name:
            logger.error(
                f"ERROR: Conversation in browser-export.json is missing 'name' field"
            )
            sys.exit(1)
        
        # Always use ID from config
        args.browser_conversation_id = conversation_info.get("id")
        if not args.browser_conversation_id:
            logger.error(
                f"ERROR: Conversation in browser-export.json is missing 'id' field"
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
                    select_conversation_from_sidebar(args.browser_conversation_id)
                except Exception as e:
                    logger.warning(f"Failed to select conversation from sidebar: {e}")
                    logger.warning("Continuing with extraction - ensure browser is positioned on the correct conversation.")

        logger.info("Browser-based DM export mode (DOM extraction)")
        logger.info(f"Conversation name: {conversation_name}")
        logger.info("Reading messages from stdin (no intermediate files)")

        # Initialize processor for conversation filtering only
        processor = BrowserResponseProcessor()
        
        # Extract messages from DOM
        # Messages must be provided via stdin (JSON format) - no intermediate files
        # Browser exports use the same code path as --export-history
        all_messages = []
        import json
        
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
            all_messages = response_data.get("messages", [])
            logger.info(f"Loaded {len(all_messages)} messages from stdin")
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse JSON from stdin: {e}")
            sys.exit(1)
        except Exception as e:
            logger.error(f"Failed to read from stdin: {e}", exc_info=True)
            sys.exit(1)
        
        if not all_messages:
            logger.error("No messages found in input")
            sys.exit(1)

        # Filter messages by conversation participants (browser exports may contain multiple conversations)
        all_messages = processor._filter_by_conversation_participants(all_messages, conversation_name)
        if not all_messages:
            logger.warning("No messages found after filtering by conversation participants")
            sys.exit(1)
        logger.info(f"Filtered to {len(all_messages)} messages from conversation participants")

        # Determine oldest timestamp for incremental fetching
        # Initialize Google Drive client early if uploading to Drive (needed for incremental export check)
        google_drive_client = None
        sanitized_folder_name = None
        safe_conversation_name = None
        google_drive_folder_id = None
        folder_id = None
        
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

            google_drive_client = GoogleDriveClient(google_drive_credentials_file)
            sanitized_folder_name = sanitize_folder_name(conversation_name)
            safe_conversation_name = sanitize_filename(conversation_name)
            google_drive_folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "").strip() or None
            
            # Create or get folder to check for metadata
            folder_id = google_drive_client.create_folder(
                sanitized_folder_name, google_drive_folder_id
            )
        
        # Get oldest timestamp using unified function
        oldest_ts = get_oldest_timestamp_for_export(
            google_drive_client=google_drive_client,
            folder_id=folder_id,
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

        latest_ts = None
        if args.end_date:
            latest_ts = convert_date_to_timestamp(args.end_date, is_end_date=True)
            if latest_ts is None:
                logger.error(f"Invalid end date format: {args.end_date}")
                sys.exit(1)
            logger.info(f"Filtering messages until: {args.end_date} ({latest_ts})")

        # Validate date range logic
        if oldest_ts and latest_ts:
            if float(oldest_ts) > float(latest_ts):
                logger.error(
                    f"Start date ({args.start_date or 'last export'}) must be before end date ({args.end_date})"
                )
                sys.exit(1)

        # Filter messages by date range if specified
        if oldest_ts or latest_ts:
            filtered_messages = []
            oldest_float = float(oldest_ts) if oldest_ts else 0.0
            latest_float = float(latest_ts) if latest_ts else float("inf")
            
            for msg in all_messages:
                msg_ts = msg.get("ts")
                if msg_ts:
                    msg_ts_float = float(msg_ts)
                    if msg_ts_float >= oldest_float and msg_ts_float <= latest_float:
                        filtered_messages.append(msg)
            
            logger.info(
                f"Filtered {len(all_messages)} messages to {len(filtered_messages)} "
                f"messages in date range"
            )
            all_messages = filtered_messages

        if not all_messages:
            logger.warning("No messages found after date range filtering")
            sys.exit(1)

        # Check if uploading to Google Drive
        if args.upload_to_drive:
            # Google Drive client may have been initialized earlier for incremental export check
            if google_drive_client is None:
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

                google_drive_client = GoogleDriveClient(google_drive_credentials_file)
                sanitized_folder_name = sanitize_folder_name(conversation_name)
                safe_conversation_name = sanitize_filename(conversation_name)
                google_drive_folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "").strip() or None

            # Create or get folder (may have been created earlier for metadata check)
            folder_id = google_drive_client.create_folder(
                sanitized_folder_name, google_drive_folder_id
            )
            if not folder_id:
                logger.error(f"Failed to create/get folder for {conversation_name}")
                sys.exit(1)

            logger.info(f"Using folder: {sanitized_folder_name} ({folder_id})")

            # Upload messages using unified function
            stats = upload_messages_to_drive(
                messages=all_messages,
                conversation_name=conversation_name,
                conversation_id=args.browser_conversation_id,
                google_drive_client=google_drive_client,
                google_drive_folder_id=google_drive_folder_id,
                slack_client=None,
                people_cache=None,
                use_display_names=True,
            )

            # Share folder with members (same logic as Slack export)
            if conversation_info and folder_id:
                # Initialize Slack client for sharing (required for member lookup)
                slack_bot_token = os.getenv("SLACK_BOT_TOKEN", "").strip()
                if slack_bot_token:
                    try:
                        slack_client = SlackClient(slack_bot_token)
                        # Load people cache and opt-out sets
                        people_cache, no_notifications_set, no_share_set = _load_people_cache()
                        
                        # Add sharing stats to stats dict
                        stats["shared"] = 0
                        stats["share_failed"] = 0
                        
                        # Share folder using same logic as Slack export
                        share_folder_for_browser_export(
                            google_drive_client,
                            folder_id,
                            slack_client,
                            conversation_info,
                            conversation_name,
                            no_notifications_set,
                            no_share_set,
                            stats,
                        )
                    except Exception as e:
                        logger.warning(f"Failed to share folder (Slack client error): {e}")
                else:
                    logger.warning("SLACK_BOT_TOKEN not set - skipping folder sharing. Set token to enable sharing.")

            # Log statistics
            _log_statistics(stats, upload_to_drive=True)

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
    elif not any([args.make_ref_files, args.export_history, args.upload_to_drive]):
        parser.print_help()
    else:
        main(args)
