"""
Message processing utilities for formatting, grouping, and preprocessing Slack messages.
"""
import re
from datetime import datetime, timezone
from calendar import monthrange
from typing import Any, Dict, List, Optional, Tuple

from src.utils import format_timestamp
from src.slack_client import SlackClient

# Constants
SECONDS_PER_DAY = 86400  # Seconds in a day
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
    slack_client: Optional[SlackClient],
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
    from src.utils import setup_logging
    logger = setup_logging()
    
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
        if not use_display_names and slack_client:
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
    from src.utils import setup_logging
    logger = setup_logging()
    
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


def filter_messages_by_date_range(
    messages: List[Dict[str, Any]],
    oldest_ts: Optional[str],
    latest_ts: Optional[str],
    validate_range: bool = True,
    max_date_range_days: Optional[int] = None,
) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """Filter messages by date range and validate date range logic.

    Args:
        messages: List of message dictionaries to filter
        oldest_ts: Optional oldest timestamp (Unix timestamp string)
        latest_ts: Optional latest timestamp (Unix timestamp string)
        validate_range: Whether to validate that oldest_ts < latest_ts (default: True)
        max_date_range_days: Optional maximum date range in days (for validation)

    Returns:
        Tuple of (filtered_messages, error_message)
        - filtered_messages: Filtered list of messages
        - error_message: None if successful, error message string if validation failed
    """
    from src.utils import setup_logging
    logger = setup_logging()
    
    # Warn if validation is explicitly disabled (security/validation concern)
    if not validate_range:
        logger.warning(
            "Date range validation is disabled. This may allow invalid date ranges to be processed. "
            "Consider enabling validation for safer operation."
        )
    
    # Validate date range logic
    if validate_range and oldest_ts and latest_ts:
        try:
            oldest_float_val = float(oldest_ts)
            latest_float_val = float(latest_ts)
        except (ValueError, TypeError) as e:
            logger.error(f"Invalid timestamp format in date range validation: oldest={oldest_ts}, latest={latest_ts}", exc_info=True)
            return [], f"Invalid timestamp format for date range validation"
        
        if oldest_float_val > latest_float_val:
            return [], f"Start date ({oldest_ts}) must be before end date ({latest_ts})"

        # Validate date range doesn't exceed maximum if specified
        if max_date_range_days:
            date_range_days = (latest_float_val - oldest_float_val) / SECONDS_PER_DAY
            if date_range_days > max_date_range_days:
                return [], (
                    f"Date range ({date_range_days:.0f} days) exceeds maximum allowed "
                    f"({max_date_range_days} days). Use --bulk-export to override."
                )

    # Filter messages by date range if specified
    if oldest_ts or latest_ts:
        filtered_messages = []
        
        # Validate and convert timestamps with error handling
        try:
            oldest_float = float(oldest_ts) if oldest_ts else 0.0
        except (ValueError, TypeError) as e:
            logger.error(f"Invalid oldest_ts format: {oldest_ts}", exc_info=True)
            return [], f"Invalid timestamp format for oldest_ts: {oldest_ts}"
        
        try:
            latest_float = float(latest_ts) if latest_ts else float("inf")
        except (ValueError, TypeError) as e:
            logger.error(f"Invalid latest_ts format: {latest_ts}", exc_info=True)
            return [], f"Invalid timestamp format for latest_ts: {latest_ts}"

        for msg in messages:
            msg_ts = msg.get("ts")
            if msg_ts:
                try:
                    msg_ts_float = float(msg_ts)
                    if msg_ts_float >= oldest_float and msg_ts_float <= latest_float:
                        filtered_messages.append(msg)
                except (ValueError, TypeError):
                    # Skip messages with invalid timestamps
                    logger.warning(f"Skipping message with invalid timestamp: {msg_ts}")
                    continue

        logger.info(
            f"Filtered {len(messages)} messages to {len(filtered_messages)} "
            f"messages in date range"
        )
        return filtered_messages, None

    # No filtering needed
    return messages, None


def validate_message(msg: Dict[str, Any]) -> bool:
    """Validate message has required fields.
    
    Args:
        msg: Message dictionary to validate
        
    Returns:
        True if message has required fields, False otherwise
    """
    if not isinstance(msg, dict):
        return False
    # Messages should have at least a timestamp (ts) field
    # Text field may be empty for some message types (e.g., file uploads)
    return "ts" in msg
