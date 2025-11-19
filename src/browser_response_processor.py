"""
Process browser-extracted Slack messages and convert to export format.

This module processes messages extracted from Slack's DOM (via DOM extraction)
and converts them into the same format used by the main export functionality.
It can also process messages from API responses if needed, but DOM extraction
is the recommended method due to Slack's client-side caching.
"""

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from src.utils import format_timestamp, sanitize_filename, sanitize_folder_name, setup_logging

logger = setup_logging()


class BrowserResponseProcessor:
    """Process browser-extracted Slack messages into export format.
    
    Handles messages extracted from DOM or API responses, with DOM extraction
    being the recommended method.
    """

    def __init__(self, user_map: Optional[Dict[str, str]] = None):
        """Initialize processor.

        Args:
            user_map: Optional mapping of user IDs to display names.
                     If None, user IDs will be discovered from messages.
        """
        self.user_map: Dict[str, str] = user_map or {}
        self.processed_message_ids: Set[str] = set()

    def discover_user_ids(self, messages: List[Dict[str, Any]]) -> Dict[str, str]:
        """Discover user IDs and names from messages.

        Args:
            messages: List of message dictionaries

        Returns:
            Dictionary mapping user IDs to display names.
            If messages contain display names instead of IDs, creates a mapping
            that preserves the display names.
        """
        user_map = {}
        for msg in messages:
            user_id = msg.get("user")
            if not user_id:
                continue
            
            # Check if this is a user ID (starts with U, length > 8) or a display name
            if user_id.startswith("U") and len(user_id) > 8:
                # It's a user ID - use it as key, keep existing mapping or use ID as fallback
                if user_id not in user_map:
                    user_map[user_id] = self.user_map.get(user_id, user_id)
            else:
                # It's likely a display name from DOM extraction
                # Create a mapping from name to name (identity mapping)
                # This preserves the display name we extracted
                if user_id not in user_map.values():
                    # Use a synthetic key or the name itself
                    # For browser export, we'll use the display name as both key and value
                    # This allows the format_message functions to work correctly
                    user_map[user_id] = user_id

        # Update with any existing mappings
        user_map.update(self.user_map)
        return user_map

    def parse_timestamp(self, ts: str) -> datetime:
        """Convert Slack timestamp to datetime object.

        Args:
            ts: Slack timestamp string (e.g., "1729263032.513419")

        Returns:
            datetime object
        """
        return datetime.fromtimestamp(float(ts))

    def replace_user_ids_in_text(
        self, text: str, user_map: Dict[str, str]
    ) -> str:
        """Replace user IDs in message text with user display names.

        Handles Slack user mention formats:
        - <@U1234567890> (standard mention format)
        - @U1234567890 (mention without angle brackets)

        Args:
            text: Message text that may contain user IDs
            user_map: Mapping of user IDs to display names

        Returns:
            Text with user IDs replaced by display names (if found in user_map)
        """
        if not text:
            return text

        import re

        # Pattern to match Slack user mentions: <@U...> or @U...
        pattern = r"<@(U[A-Z0-9]+)>|@(U[A-Z0-9]+)"

        def replace_match(match: re.Match) -> str:
            # Extract user ID from either capture group
            user_id = match.group(1) or match.group(2)
            if not user_id:
                return match.group(0)  # Return original if no match

            # Look up display name in user_map
            display_name = user_map.get(user_id)
            if display_name:
                return f"@{display_name}"
            # If not found, return original mention
            return match.group(0)

        return re.sub(pattern, replace_match, text)

    def format_message_text(self, message: Dict[str, Any]) -> str:
        """Extract and format message text, handling blocks and rich text.

        Args:
            message: Message dictionary

        Returns:
            Formatted message text
        """
        text = message.get("text", "")

        # If no text but has blocks, extract from blocks
        if not text and message.get("blocks"):
            text_parts = []
            for block in message.get("blocks", []):
                if block.get("elements"):
                    for element in block["elements"]:
                        if element.get("type") == "rich_text_section":
                            for item in element.get("elements", []):
                                if item.get("type") == "text":
                                    text_parts.append(item.get("text", ""))
                                elif item.get("type") == "emoji":
                                    text_parts.append(f":{item.get('name', '')}:")
                                elif item.get("type") == "link":
                                    text_parts.append(item.get("url", ""))
            text = "".join(text_parts)

        return text

    def format_message_for_export(
        self, message: Dict[str, Any], user_map: Dict[str, str]
    ) -> str:
        """Format a single message for export output (markdown format for local files).

        Args:
            message: Message dictionary
            user_map: Mapping of user IDs to display names

        Returns:
            Formatted message string
        """
        ts = message.get("ts", "")
        user_id = message.get("user", "")
        text = self.format_message_text(message)

        # Get user name (match main export: "Unknown User" if no user_id)
        user_name = "Unknown User"
        if user_id:
            if user_id.startswith("U") and len(user_id) > 8:
                user_name = user_map.get(user_id, user_id)
            else:
                # Display name from DOM extraction
                user_name = user_id

        # Parse timestamp
        try:
            dt = self.parse_timestamp(ts)
            time_str = dt.strftime("%I:%M %p")
        except (ValueError, TypeError):
            time_str = ts

        # Build message parts
        parts = [f"**{user_name}** - {time_str}"]

        # Handle edited messages
        if message.get("edited"):
            parts[-1] += " (edited)"

        # Add message text
        if text:
            parts.append(text)

        # Handle reactions
        if message.get("reactions"):
            reaction_list = []
            for reaction in message["reactions"]:
                reaction_list.append(f"{reaction['name']} ({reaction['count']})")
            parts.append(f"Reactions: {', '.join(reaction_list)}")

        # Handle files
        if message.get("files"):
            for file in message["files"]:
                file_name = file.get("name", "Unknown file")
                parts.append(f"[File: {file_name}]")

        # Handle attachments
        if message.get("attachments"):
            for att in message["attachments"]:
                if att.get("title"):
                    parts.append(f"Attachment: {att['title']}")
                if att.get("text"):
                    parts.append(att["text"])

        return "\n".join(parts) + "\n"

    def format_message_for_google_doc(
        self, message: Dict[str, Any], user_map: Dict[str, str]
    ) -> Tuple[str, str]:
        """Format a message for Google Doc export (matches main export format).

        Args:
            message: Message dictionary
            user_map: Mapping of user IDs to display names

        Returns:
            Tuple of (formatted_message, timestamp_string)
        """
        ts = message.get("ts", "")
        user_id = message.get("user", "")
        text = self.format_message_text(message)
        files = message.get("files", [])

        # Get user name (match main export: "Unknown User" if no user_id)
        user_name = "Unknown User"
        if user_id:
            if user_id.startswith("U") and len(user_id) > 8:
                user_name = user_map.get(user_id, user_id)
            else:
                # Display name from DOM extraction
                user_name = user_id

        # Format timestamp like main export
        formatted_time = format_timestamp(ts)
        if formatted_time is None:
            formatted_time = str(ts) if ts else "[Invalid timestamp]"

        # Handle files like main export
        if not text and files:
            text = "[File attached]"
        elif text and files:
            text += " [File attached]"

        # Replace newlines with indentation for multi-line messages
        text = text.replace("\n", "\n    ")

        # Format like main export: [timestamp] name: text
        formatted_message = f"[{formatted_time}] {user_name}: {text}"

        return formatted_message, ts

    def group_messages_by_date(
        self, messages: List[Dict[str, Any]]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Group messages by date.

        Args:
            messages: List of message dictionaries

        Returns:
            Dictionary mapping date strings (YYYY-MM-DD) to lists of messages
        """
        grouped = defaultdict(list)
        for msg in messages:
            ts = msg.get("ts", "")
            try:
                dt = self.parse_timestamp(ts)
                date_key = dt.strftime("%Y-%m-%d")
                grouped[date_key].append(msg)
            except (ValueError, TypeError):
                grouped["unknown"].append(msg)

        # Sort messages within each date by timestamp
        for date_key in grouped:
            grouped[date_key].sort(key=lambda x: float(x.get("ts", "0")))

        return grouped

    def preprocess_messages_for_google_doc(
        self, messages: List[Dict[str, Any]], user_map: Dict[str, str]
    ) -> str:
        """Process messages into Google Doc format (matches main export preprocess_history).

        Args:
            messages: List of message dictionaries
            user_map: Mapping of user IDs to display names

        Returns:
            Formatted text string ready for Google Doc
        """
        threads = {}
        for message in messages:
            text = self.format_message_text(message)
            files = message.get("files", [])

            # If no text and no files, skip
            if not text and not files:
                continue

            # If no text but has files, use a placeholder
            if not text and files:
                text = "[File attached]"
            # If text and files, append placeholder
            elif text and files:
                text += " [File attached]"

            # Replace user IDs in message text with display names from user_map
            text = self.replace_user_ids_in_text(text, user_map)

            thread_key = message.get("thread_ts", message.get("ts"))
            if not thread_key:
                continue

            if thread_key not in threads:
                threads[thread_key] = []

            ts = message.get("ts")
            user_id = message.get("user", "")
            # Handle both user IDs and display names in user_map
            # Match main export behavior: use "Unknown User" if no user_id, otherwise look up
            name = "Unknown User"
            if user_id:
                if user_id.startswith("U") and len(user_id) > 8:
                    # Likely a user ID, look it up
                    name = user_map.get(user_id, user_id)
                else:
                    # Likely a display name from DOM extraction, use it directly
                    name = user_id

            text = text.replace("\n", "\n    ")

            threads[thread_key].append((ts, name, text))

        sorted_thread_keys = sorted(threads.keys())
        output_lines = []
        for thread_key in sorted_thread_keys:
            messages_in_thread = sorted(threads[thread_key], key=lambda m: m[0] if m[0] else "")

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

    def process_responses(
        self,
        response_files: List[Path],
        output_dir: Path,
        conversation_name: str = "DM",
        oldest_ts: Optional[str] = None,
        latest_ts: Optional[str] = None,
    ) -> Tuple[int, Dict[str, int]]:
        """Process message files and write messages to export files.

        Args:
            response_files: List of paths to message JSON files (from DOM extraction or API responses)
            output_dir: Directory to write output files
            conversation_name: Name of the conversation (for filename)
            oldest_ts: Optional oldest timestamp (Unix timestamp string) to filter messages
            latest_ts: Optional latest timestamp (Unix timestamp string) to filter messages

        Returns:
            Tuple of (total_messages_written, dict mapping dates to message counts)
        """
        output_dir.mkdir(parents=True, exist_ok=True)

        # Load all responses
        all_messages = []
        for response_file in response_files:
            if not response_file.exists():
                logger.warning(f"Response file does not exist: {response_file}")
                continue

            logger.info(f"Processing {response_file.name}...")
            try:
                with open(response_file, "r", encoding="utf-8") as f:
                    response_data = json.load(f)

                messages = response_data.get("messages", [])
                if not isinstance(messages, list):
                    logger.warning(f"No messages found in {response_file.name}")
                    continue

                all_messages.extend(messages)
                logger.info(f"  Found {len(messages)} messages")
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"Failed to load {response_file}: {e}")

        if not all_messages:
            logger.warning("No messages found in any response files")
            return 0, {}

        # Filter by date range if specified
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
            return 0, {}

        # Discover user IDs
        self.user_map.update(self.discover_user_ids(all_messages))
        logger.info(f"Discovered user IDs: {self.user_map}")

        # Deduplicate messages by timestamp
        unique_messages = {}
        for msg in all_messages:
            msg_id = msg.get("ts")
            if msg_id and msg_id not in self.processed_message_ids:
                unique_messages[msg_id] = msg
                self.processed_message_ids.add(msg_id)

        logger.info(f"Found {len(unique_messages)} unique messages (from {len(all_messages)} total)")

        # Group by date
        grouped = self.group_messages_by_date(list(unique_messages.values()))

        # Write to files
        total_written = 0
        date_counts: Dict[str, int] = {}

        for date_key in sorted(grouped.keys()):
            if date_key == "unknown":
                continue

            date_messages = grouped[date_key]
            sanitized_name = sanitize_filename(conversation_name)
            filename = f"{date_key}-{sanitized_name}.txt"
            filepath = output_dir / filename

            # Write messages to file
            written = self._write_messages_to_file(date_messages, filepath)
            total_written += written
            date_counts[date_key] = written
            logger.info(f"Wrote {written} messages to {filepath.name}")

        logger.info(f"Total: {total_written} messages written across {len(date_counts)} dates")
        return total_written, date_counts

    def _write_messages_to_file(
        self, messages: List[Dict[str, Any]], filepath: Path
    ) -> int:
        """Write messages to a file, appending to existing content if present.

        Args:
            messages: List of message dictionaries for a single date
            filepath: Path to output file

        Returns:
            Number of messages written
        """
        # Sort messages by timestamp
        messages.sort(key=lambda x: float(x.get("ts", "0")))

        # Build output lines
        output_lines = []
        current_date = None

        for msg in messages:
            ts = msg.get("ts", "")
            try:
                dt = self.parse_timestamp(ts)
                msg_date = dt.strftime("%Y-%m-%d")

                # Add date separator if date changed
                if msg_date != current_date:
                    if current_date is not None:
                        output_lines.append("")  # Empty line between dates
                    date_header = dt.strftime("%A, %B %d, %Y")
                    output_lines.append(f"## {date_header}\n")
                    current_date = msg_date

                # Format and add message
                formatted = self.format_message_for_export(msg, self.user_map)
                output_lines.append(formatted)
                output_lines.append("")  # Empty line between messages

            except Exception as e:
                logger.error(f"Error processing message {ts}: {e}")
                output_lines.append(f"**Error processing message**\n{json.dumps(msg, indent=2)}\n\n")

        # Write to file (append if exists)
        content = "\n".join(output_lines)
        if filepath.exists():
            existing = filepath.read_text()
            content = existing + "\n" + content

        filepath.write_text(content, encoding="utf-8")
        return len(messages)

    def process_responses_for_google_drive(
        self,
        response_files: List[Path],
        conversation_name: str,
        conversation_id: Optional[str] = None,
        oldest_ts: Optional[str] = None,
        latest_ts: Optional[str] = None,
    ) -> Tuple[Dict[str, List[Dict[str, Any]]], Dict[str, str]]:
        """Process responses and group by date for Google Drive upload.

        Args:
            response_files: List of paths to message JSON files (from DOM extraction or API responses)
            conversation_name: Name of the conversation
            conversation_id: Optional conversation ID (for metadata)
            oldest_ts: Optional oldest timestamp (Unix timestamp string) to filter messages
            latest_ts: Optional latest timestamp (Unix timestamp string) to filter messages

        Returns:
            Tuple of (daily_groups dict mapping YYYYMMDD to messages, user_map)
        """
        # Load all responses
        all_messages = []
        for response_file in response_files:
            if not response_file.exists():
                logger.warning(f"Response file does not exist: {response_file}")
                continue

            logger.info(f"Processing {response_file.name}...")
            try:
                with open(response_file, "r", encoding="utf-8") as f:
                    response_data = json.load(f)

                messages = response_data.get("messages", [])
                if not isinstance(messages, list):
                    logger.warning(f"No messages found in {response_file.name}")
                    continue

                all_messages.extend(messages)
                logger.info(f"  Found {len(messages)} messages")
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"Failed to load {response_file}: {e}")

        if not all_messages:
            logger.warning("No messages found in any response files")
            return {}, {}

        # Filter by date range if specified
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
            return {}, {}

        # Discover user IDs
        self.user_map.update(self.discover_user_ids(all_messages))
        logger.info(f"Discovered user IDs: {self.user_map}")

        # Deduplicate messages by timestamp
        unique_messages = {}
        for msg in all_messages:
            msg_id = msg.get("ts")
            if msg_id and msg_id not in self.processed_message_ids:
                unique_messages[msg_id] = msg
                self.processed_message_ids.add(msg_id)

        logger.info(f"Found {len(unique_messages)} unique messages (from {len(all_messages)} total)")

        # Group by date (YYYYMMDD format like main export)
        daily_groups = {}
        for msg in unique_messages.values():
            ts = msg.get("ts", "")
            try:
                dt = self.parse_timestamp(ts)
                dt_utc = dt.replace(tzinfo=timezone.utc)
                date_key = dt_utc.strftime("%Y%m%d")  # YYYYMMDD format
                if date_key not in daily_groups:
                    daily_groups[date_key] = []
                daily_groups[date_key].append(msg)
            except (ValueError, TypeError):
                continue

        # Sort messages within each day by timestamp
        for date_key in daily_groups:
            daily_groups[date_key].sort(key=lambda x: float(x.get("ts", "0")))

        return daily_groups, self.user_map
