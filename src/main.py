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
) -> str:
    """Processes Slack history into a human-readable format."""
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

        # Replace user IDs in message text with user names
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
            # Check cache first
            if people_cache and user_id in people_cache:
                name = people_cache[user_id]
            else:
                user_info = slack_client.get_user_info(user_id)
                if user_info:
                    name = user_info.get("displayName", message.get("username", user_id))
                    # Update cache for future use
                    if people_cache is not None:
                        people_cache[user_id] = name

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
            # If --start-date is explicitly provided, use it; otherwise check Google Drive for last export
            oldest_ts = None
            if args.start_date:
                oldest_ts = convert_date_to_timestamp(args.start_date)
                if oldest_ts is None:
                    logger.error(f"Invalid start date format: {args.start_date}")
                    stats["skipped"] += 1
                    continue
                logger.info(f"Using explicit start date: {args.start_date}")
            elif args.upload_to_drive:
                # Check Google Drive for last export timestamp (stateless - works in CI/CD)
                sanitized_folder_name = sanitized_names["folder"]
                safe_channel_name = sanitized_names["file"]
                folder_id = google_drive_client.create_folder(
                    sanitized_folder_name, google_drive_folder_id
                )
                if folder_id:
                    last_export_ts = google_drive_client.get_latest_export_timestamp(
                        folder_id, safe_channel_name
                    )
                    if last_export_ts:
                        oldest_ts = last_export_ts
                        last_export_dt = datetime.fromtimestamp(
                            float(last_export_ts), tz=timezone.utc
                        )
                        logger.info(
                            f"Fetching messages since last export: {last_export_dt.strftime('%Y-%m-%d %H:%M:%S UTC')}"
                        )
                    else:
                        logger.info("No previous export found in Drive, fetching all messages")
                else:
                    logger.info("Could not access/create folder, fetching all messages")
            else:
                logger.info(
                    "Not uploading to Drive, fetching all messages (use --start-date for incremental export)"
                )

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

            # Group messages by date for daily Google Docs (when uploading to Drive)
            if args.upload_to_drive:
                # Group messages by date for daily file creation
                daily_groups = group_messages_by_date(history)
                logger.info(
                    f"Grouped {len(history)} messages into {len(daily_groups)} daily group(s)"
                )

                # Process each daily group
                daily_docs_created = []
                sanitized_folder_name = sanitized_names["folder"]
                folder_id = google_drive_client.create_folder(
                    sanitized_folder_name, google_drive_folder_id
                )

                if folder_id:
                    # Sort dates chronologically
                    sorted_dates = sorted(daily_groups.keys())

                    for date_key in sorted_dates:
                        daily_messages = daily_groups[date_key]
                        logger.info(
                            f"Processing {len(daily_messages)} messages for date {date_key}"
                        )

                        # Process messages for this day
                        processed_messages = preprocess_history(
                            daily_messages, slack_client, people_cache
                        )

                        # Check for empty history after processing
                        if not processed_messages or not processed_messages.strip():
                            logger.warning(
                                f"No processable content found for {date_key} of {channel_name}. Skipping."
                            )
                            continue

                        # Create doc name: channel name slack messages yyyymmdd
                        # Sanitize doc name to ensure it's valid for Google Drive (255 char limit, no invalid chars)
                        doc_name_base = f"{channel_name} slack messages {date_key}"
                        doc_name = sanitize_folder_name(doc_name_base)

                        # Check if doc already exists to determine if we need a header
                        escaped_doc_name = google_drive_client._escape_drive_query_string(doc_name)
                        escaped_folder_id = google_drive_client._escape_drive_query_string(
                            folder_id
                        )
                        query = (
                            f"name='{escaped_doc_name}' and '{escaped_folder_id}' in parents "
                            f"and mimeType='application/vnd.google-apps.document' and trashed=false"
                        )

                        doc_exists = False
                        try:
                            google_drive_client._rate_limit()
                            results = (
                                google_drive_client.service.files()
                                .list(q=query, fields="files(id, name)", pageSize=1)
                                .execute()
                            )
                            if results.get("files"):
                                doc_exists = True
                        except Exception as e:
                            logger.debug(
                                f"Error checking for existing doc '{doc_name}': {e}, assuming new doc"
                            )
                            # Assume new doc if check fails

                        # Prepare content: add header only for new docs
                        if doc_exists:
                            # Append separator and messages (no header for existing docs)
                            content_to_add = f"\n\n{'='*80}\n\n{processed_messages}"
                        else:
                            # Add full header for new docs
                            export_date = datetime.now(timezone.utc).strftime(
                                "%Y-%m-%d %H:%M:%S UTC"
                            )
                            date_obj = datetime.strptime(date_key, "%Y%m%d").replace(
                                tzinfo=timezone.utc
                            )
                            date_display = date_obj.strftime("%Y-%m-%d")
                            metadata_header = f"""Slack Conversation Export
Channel: {channel_name}
Channel ID: {channel_id}
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
                                f"Failed to create Google Doc for {date_key} of {channel_name}"
                            )
                            stats["upload_failed"] += 1
                        else:
                            daily_docs_created.append((doc_id, date_key, daily_messages))
                            stats["uploaded"] += 1
                            stats["processed"] += 1
                            stats["total_messages"] += len(daily_messages)
                            logger.info(f"Created/updated Google Doc for {date_key}")

                    # Save export metadata with latest timestamp from all daily groups
                    if history:
                        latest_message_ts = max(float(msg.get("ts", 0)) for msg in history)
                        safe_channel_name = sanitized_names["file"]
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
        default="browser_exports/api_responses",
        help="Directory containing captured API responses for browser export (default: browser_exports/api_responses).",
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
        help="Name of the conversation for browser export filename (default: DM).",
    )
    parser.add_argument(
        "--browser-conversation-id",
        type=str,
        help="Optional conversation ID for browser export metadata.",
    )

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
        from pathlib import Path
        from datetime import datetime, timezone
        from src.browser_response_processor import BrowserResponseProcessor
        from src.browser_scraper import extract_messages_from_dom

        response_dir = Path(args.browser_response_dir)
        output_dir = Path(args.browser_output_dir)
        conversation_name = args.browser_conversation_name

        logger.info("Browser-based DM export mode (DOM extraction)")
        logger.info(f"Response directory: {response_dir}")
        logger.info(f"Output directory: {output_dir}")
        logger.info(f"Conversation name: {conversation_name}")

        processor = BrowserResponseProcessor()
        
        # DOM extraction is the only method - look for response_dom_extraction.json
        response_dir.mkdir(parents=True, exist_ok=True)
        dom_response_file = response_dir / "response_dom_extraction.json"
        
        if not dom_response_file.exists():
            logger.error("DOM extraction file not found. Please extract messages first.")
            logger.info("")
            logger.info("To extract messages from DOM:")
            logger.info("1. Open Slack in a browser and navigate to the conversation")
            logger.info("2. Scroll to load all messages in the date range")
            logger.info("3. Use MCP chrome-devtools tools to run DOM extraction")
            logger.info("   Example: Use scripts/extract_dom_messages.py or call extract_messages_from_dom()")
            logger.info("4. Save the result to: response_dom_extraction.json")
            logger.info("")
            logger.info("See ReadMe.md for detailed instructions.")
            sys.exit(1)
        
        logger.info(f"Found DOM extraction file: {dom_response_file}")
        response_files = [dom_response_file]

        # Parse date range if provided
        oldest_ts = None
        latest_ts = None
        if args.start_date:
            oldest_ts = convert_date_to_timestamp(args.start_date)
            if oldest_ts is None:
                logger.error(f"Invalid start date format: {args.start_date}")
                sys.exit(1)
            logger.info(f"Filtering messages from: {args.start_date} ({oldest_ts})")

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
                    f"Start date ({args.start_date}) must be before end date ({args.end_date})"
                )
                sys.exit(1)

        # Check if uploading to Google Drive
        if args.upload_to_drive:
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

            google_drive_folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "").strip() or None

            # Initialize Google Drive client
            google_drive_client = GoogleDriveClient(google_drive_credentials_file)

            # Process responses for Google Drive
            logger.info(f"Processing {len(response_files)} response files for Google Drive upload")
            daily_groups, user_map = processor.process_responses_for_google_drive(
                response_files, conversation_name, oldest_ts=oldest_ts, latest_ts=latest_ts
            )

            if not daily_groups:
                logger.warning("No messages found to upload")
                sys.exit(1)

            # Sanitize conversation name for folder
            sanitized_folder_name = sanitize_folder_name(conversation_name)

            # Create or get folder
            folder_id = google_drive_client.create_folder(
                sanitized_folder_name, google_drive_folder_id
            )
            if not folder_id:
                logger.error(f"Failed to create/get folder for {conversation_name}")
                sys.exit(1)

            logger.info(f"Using folder: {sanitized_folder_name} ({folder_id})")

            # Process each day and create/update Google Docs
            stats = {
                "processed": 0,
                "uploaded": 0,
                "upload_failed": 0,
                "total_messages": 0,
            }

            for date_key in sorted(daily_groups.keys()):
                daily_messages = daily_groups[date_key]
                logger.info(
                    f"Processing {len(daily_messages)} messages for date {date_key}"
                )

                # Process messages for this day (format like main export)
                processed_messages = processor.preprocess_messages_for_google_doc(
                    daily_messages, user_map
                )

                if not processed_messages or not processed_messages.strip():
                    logger.warning(
                        f"No processable content found for {date_key} of {conversation_name}. Skipping."
                    )
                    continue

                # Create doc name: conversation name slack messages yyyymmdd
                doc_name_base = f"{conversation_name} slack messages {date_key}"
                doc_name = sanitize_folder_name(doc_name_base)

                # Check if doc already exists
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
                        .list(q=query, fields="files(id, name)", pageSize=1)
                        .execute()
                    )
                    if results.get("files"):
                        doc_exists = True
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
                    metadata_header = f"""Slack Conversation Export
Channel: {conversation_name}
Channel ID: {"[Browser Export - No ID]"}
Export Date: {export_date}
Date: {date_display}
Total Messages: {len(daily_messages)}

{'='*80}

"""
                    content_to_add = metadata_header + processed_messages

                # Create or update Google Doc
                doc_id = google_drive_client.create_or_update_google_doc(
                    doc_name, content_to_add, folder_id, overwrite=False
                )

                if not doc_id:
                    logger.error(f"Failed to create Google Doc for {date_key} of {conversation_name}")
                    stats["upload_failed"] += 1
                else:
                    stats["uploaded"] += 1
                    stats["processed"] += 1
                    stats["total_messages"] += len(daily_messages)
                    logger.info(f"Created/updated Google Doc for {date_key}")

            # Save export metadata
            if daily_groups:
                # Get latest timestamp from all messages
                all_messages_flat = []
                for messages in daily_groups.values():
                    all_messages_flat.extend(messages)
                if all_messages_flat:
                    latest_message_ts = max(float(msg.get("ts", 0)) for msg in all_messages_flat)
                    safe_conversation_name = sanitize_filename(conversation_name)
                    google_drive_client.save_export_metadata(
                        folder_id, safe_conversation_name, str(latest_message_ts)
                    )
                    logger.info(f"Saved export metadata for {conversation_name}")

            # Log statistics
            logger.info("=" * 80)
            logger.info("Export Statistics:")
            logger.info(f"  Processed: {stats['processed']}")
            logger.info(f"  Uploaded to Drive: {stats['uploaded']}")
            logger.info(f"  Upload Failed: {stats['upload_failed']}")
            logger.info(f"  Total messages processed: {stats['total_messages']}")
            logger.info("=" * 80)

        else:
            # Local file export (existing behavior)
            logger.info(f"Processing {len(response_files)} response files")
            total_messages, date_counts = processor.process_responses(
                response_files, output_dir, conversation_name, oldest_ts=oldest_ts, latest_ts=latest_ts
            )
            logger.info(f"Export complete: {total_messages} messages across {len(date_counts)} dates")
    elif not any([args.make_ref_files, args.export_history, args.upload_to_drive]):
        parser.print_help()
    else:
        main(args)
