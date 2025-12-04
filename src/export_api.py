"""
Slack API export functionality.
"""
from typing import Any, Dict

from src.slack_client import SlackClient
from src.utils import setup_logging, sanitize_string_for_logging

logger = setup_logging()


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
            logger.warning(f"Group DM {sanitize_string_for_logging(channel_id)} has no members")
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
