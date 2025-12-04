"""
Google Drive upload and sharing functionality for Slack Feeder.
"""
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set, Tuple

from src.google_drive import GoogleDriveClient
from src.slack_client import SlackClient, SHARE_RATE_LIMIT_DELAY, SHARE_RATE_LIMIT_INTERVAL
from src.utils import (
    sanitize_filename,
    sanitize_folder_name,
    sanitize_string_for_logging,
    validate_email,
    validate_people_json,
    load_json_file,
)
from src.message_processing import (
    group_messages_by_date,
    preprocess_history,
    validate_message,
)

# Constants
DAILY_MESSAGE_CHUNK_SIZE = 10000  # Process daily messages in chunks of this size to manage memory
BROWSER_EXPORT_CONFIG_FILENAME = "browser-export.json"  # Default config filename
CHANNELS_CONFIG_FILENAME = "channels.json"  # Channels config filename


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


def _validate_conversation_id(conversation_id: str) -> bool:
    """Validate Slack conversation ID format.

    Args:
        conversation_id: Conversation ID to validate

    Returns:
        True if valid format, False otherwise
    """
    if not conversation_id or not isinstance(conversation_id, str):
        return False
    if len(conversation_id) < 2:
        return False
    # Slack IDs start with C (channel), D (DM), or G (group DM)
    # followed by alphanumeric characters
    return conversation_id[0] in ['C', 'D', 'G'] and conversation_id[1:].isalnum()


def _resolve_member_identifier(
    identifier: str,
    slack_client: SlackClient,
    people_cache: Optional[Dict[str, str]] = None,
    people_json: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, str]]:
    """Resolve a member identifier (user ID, email, or display name) to user info.
    
    Uses fallback chain:
    1. If identifier is a Slack user ID (starts with U), try Slack API
    2. Look up in people.json by slackId, email, or displayName
    3. Fall back to Slack API (if identifier looks like a user ID)
    
    Args:
        identifier: Member identifier (user ID, email, or display name)
        slack_client: SlackClient instance
        people_cache: Optional dict mapping slackId -> displayName
        people_json: Optional full people.json dict with "people" list
    
    Returns:
        User info dict with slackId, email, displayName, or None if not found
    """
    from src.utils import setup_logging
    logger = setup_logging()
    
    if not identifier:
        return None
    
    identifier_lower = identifier.lower().strip()
    
    # Step 1: Check if it's already a Slack user ID (starts with U)
    if identifier.startswith("U") and len(identifier) > 1:
        # Try Slack API first for user IDs
        try:
            user_info = slack_client.get_user_info(identifier)
            if user_info:
                return user_info
        except Exception as e:
            logger.debug(f"Could not get user info from Slack API for {identifier}: {e}")
    
    # Step 2: Look up in people.json
    if people_json and "people" in people_json:
        for person in people_json["people"]:
            # Match by slackId
            if person.get("slackId", "").lower() == identifier_lower:
                return {
                    "slackId": person.get("slackId", ""),
                    "email": person.get("email", ""),
                    "displayName": person.get("displayName", ""),
                }
            # Match by email
            if person.get("email", "").lower() == identifier_lower:
                return {
                    "slackId": person.get("slackId", ""),
                    "email": person.get("email", ""),
                    "displayName": person.get("displayName", ""),
                }
            # Match by displayName (case-insensitive)
            if person.get("displayName", "").lower() == identifier_lower:
                return {
                    "slackId": person.get("slackId", ""),
                    "email": person.get("email", ""),
                    "displayName": person.get("displayName", ""),
                }
    
    # Step 3: Fall back to Slack API (if identifier looks like a user ID)
    if identifier.startswith("U") and len(identifier) > 1:
        try:
            user_info = slack_client.get_user_info(identifier)
            if user_info:
                return user_info
        except Exception as e:
            logger.debug(f"Could not get user info from Slack API for {identifier}: {e}")
    
    return None


def _extract_members_from_conversation_name(
    conversation_name: str,
    slack_client: SlackClient,
    people_cache: Optional[Dict[str, str]] = None,
    people_json: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Extract participant names from conversation name and resolve them to user IDs/emails.
    
    Parses conversation names like "Tara, Jay Flowers" or "Emily Fox, Jenn Power, rzhukov, Jay Flowers"
    and resolves each name to a user ID or email using people.json or Slack API.
    
    Args:
        conversation_name: Conversation display name (e.g., "Tara, Jay Flowers")
        slack_client: SlackClient instance
        people_cache: Optional dict mapping slackId -> displayName
        people_json: Optional full people.json dict with "people" list
    
    Returns:
        List of resolved member identifiers (user IDs or emails)
    """
    from src.utils import setup_logging, sanitize_string_for_logging
    logger = setup_logging()
    
    if not conversation_name:
        return []
    
    # Split by comma and clean up names
    name_parts = [name.strip() for name in conversation_name.split(",")]
    if not name_parts:
        return []
    
    resolved_members = []
    for name in name_parts:
        if not name:
            continue
        
        # Try to resolve the name to a user ID or email
        user_info = _resolve_member_identifier(name, slack_client, people_cache, people_json)
        if user_info:
            # Prefer slackId, fall back to email
            member_id = user_info.get("slackId") or user_info.get("email")
            if member_id and member_id not in resolved_members:
                resolved_members.append(member_id)
        else:
            logger.debug(f"Could not resolve participant name '{sanitize_string_for_logging(name)}' from conversation name")
    
    return resolved_members


def _get_conversation_members(
    slack_client: SlackClient,
    conversation_id: str,
    conversation_info: Dict[str, Any],
    people_cache: Optional[Dict[str, str]] = None,
    people_json: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """Get conversation members based on conversation type.

    Handles channels, DMs, and group DMs appropriately.
    For browser exports, uses fallback chain: browser-export.json members -> people.json -> Slack API.

    Args:
        slack_client: SlackClient instance
        conversation_id: Slack conversation ID
        conversation_info: Conversation configuration dictionary
            - is_im: bool - True if DM
            - is_mpim: bool - True if group DM
            - user: Optional[str] - Other user ID for DMs
            - members: Optional[List[str]] - List of member identifiers (for browser exports)
        people_cache: Optional dict mapping slackId -> displayName
        people_json: Optional full people.json dict with "people" list

    Returns:
        List of member user IDs (or emails if user ID not available)
    """
    from src.utils import setup_logging, sanitize_string_for_logging
    logger = setup_logging()
    
    members = []
    
    # Validate conversation ID format before using it
    if not _validate_conversation_id(conversation_id):
        logger.warning(f"Invalid conversation ID format: {sanitize_string_for_logging(conversation_id)}")
        return []
    
    # For browser exports, check members list first
    browser_members = conversation_info.get("members")
    if browser_members and isinstance(browser_members, list) and len(browser_members) > 0:
        logger.debug(f"Using members list from browser-export.json for {sanitize_string_for_logging(conversation_info.get('name', conversation_id))}")
        resolved_members = []
        for member_identifier in browser_members:
            user_info = _resolve_member_identifier(member_identifier, slack_client, people_cache, people_json)
            if user_info:
                # Prefer slackId, fall back to email
                member_id = user_info.get("slackId") or user_info.get("email")
                if member_id:
                    resolved_members.append(member_id)
                else:
                    logger.warning(f"Could not resolve member identifier: {sanitize_string_for_logging(member_identifier)}")
            else:
                logger.debug(f"Could not resolve member identifier: {sanitize_string_for_logging(member_identifier)}")
        if resolved_members:
            return resolved_members
        # If members list exists but couldn't resolve any, continue to fallback logic
    
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
                logger.debug(f"Could not get user for DM {sanitize_string_for_logging(conversation_id)}: {e}")
            
            # Fallback: Extract participant names from conversation name and resolve them
            # This handles cases where Slack API fails or conversation name contains participant info
            if not members:
                conversation_name = conversation_info.get("name", "")
                if conversation_name:
                    logger.debug(f"Attempting to extract members from conversation name: {sanitize_string_for_logging(conversation_name)}")
                    extracted_members = _extract_members_from_conversation_name(
                        conversation_name, slack_client, people_cache, people_json
                    )
                    if extracted_members:
                        members = extracted_members
                        logger.info(f"Resolved {len(members)} member(s) from conversation name for {sanitize_string_for_logging(conversation_name)}")
    elif conversation_info.get("is_mpim"):
        # Group DM - get all members
        members = slack_client.get_channel_members(conversation_id)
        
        # Fallback: Extract participant names from conversation name if API fails
        if not members:
            conversation_name = conversation_info.get("name", "")
            if conversation_name:
                logger.debug(f"Attempting to extract members from group DM name: {sanitize_string_for_logging(conversation_name)}")
                extracted_members = _extract_members_from_conversation_name(
                    conversation_name, slack_client, people_cache, people_json
                )
                if extracted_members:
                    members = extracted_members
                    logger.info(f"Resolved {len(members)} member(s) from conversation name for {sanitize_string_for_logging(conversation_name)}")
    else:
        # Regular channel - get all members
        members = slack_client.get_channel_members(conversation_id)
    
    return members


def share_folder_with_conversation_members(
    google_drive_client: GoogleDriveClient,
    folder_id: str,
    slack_client: SlackClient,
    conversation_id: str,
    conversation_name: str,
    conversation_info: Dict[str, Any],
    no_notifications_set: set,
    no_share_set: set,
    stats: Dict[str, int],
    config_source: str = CHANNELS_CONFIG_FILENAME,
    people_cache: Optional[Dict[str, str]] = None,
    people_json: Optional[Dict[str, Any]] = None,
) -> None:
    """Share a Google Drive folder with conversation members and manage permissions.

    Unified function for sharing folders with both API exports (channels) and browser exports (DMs/group DMs).

    Args:
        google_drive_client: GoogleDriveClient instance
        folder_id: Google Drive folder ID
        slack_client: SlackClient instance
        conversation_id: Slack conversation ID
        conversation_name: Display name of the conversation
        conversation_info: Conversation configuration dictionary
            - share: bool - whether to share (default: True)
            - shareMembers: Optional[List[str]] - list of user IDs, emails, or display names to share with
            - is_im: bool - True if DM (for browser exports)
            - is_mpim: bool - True if group DM (for browser exports)
            - user: Optional[str] - Other user ID for DMs (for browser exports)
            - members: Optional[List[str]] - List of member identifiers (for browser exports)
        no_notifications_set: Set of emails who opted out of notifications
        no_share_set: Set of emails who opted out of being shared with
        stats: Statistics dictionary to update
        config_source: Source of config (for logging) - CHANNELS_CONFIG_FILENAME or BROWSER_EXPORT_CONFIG_FILENAME
        people_cache: Optional dict mapping slackId -> displayName
        people_json: Optional full people.json dict with "people" list
    """
    from src.utils import setup_logging, sanitize_string_for_logging
    logger = setup_logging()
    
    # Check if sharing is enabled
    should_share = conversation_info.get("share", True)
    if not should_share:
        logger.info(f"Sharing disabled for {sanitize_string_for_logging(conversation_name)} (share: false in {config_source})")
        return

    # Get conversation members based on type
    members = _get_conversation_members(slack_client, conversation_id, conversation_info, people_cache, people_json)
    if not members:
        logger.warning(f"No members found for {sanitize_string_for_logging(conversation_name)}. Skipping sharing.")
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
        # Handle both user IDs and emails
        email = None
        user_info = None
        
        # Check if member_id is already an email
        if validate_email(member_id):
            email = member_id.lower()
            # Try to get user info for email (for display name, etc.)
            user_info = _resolve_member_identifier(member_id, slack_client, people_cache, people_json)
        else:
            # Assume it's a user ID, get user info
            user_info = slack_client.get_user_info(member_id)
            if user_info and user_info.get("email"):
                email = user_info["email"].lower()
        
        if email and validate_email(email):
            # Check if member should be shared with (respects shareMembers and no_share_set)
            if email not in no_share_set:
                if _should_share_with_member(member_id, user_info, share_members):
                    current_member_emails.add(email)

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
                logger.debug(f"Error revoking access for {perm_email}: {e}", exc_info=True)
                revoke_errors.append(f"{perm_email}: {str(e)}")

    if revoked_count > 0:
        logger.info(f"Revoked access for {revoked_count} user(s) no longer in {sanitize_string_for_logging(conversation_name)}")
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

        # Handle both user IDs and emails
        email = None
        user_info = None
        
        # Check if member_id is already an email
        if validate_email(member_id):
            email = member_id.lower()
            # Try to get user info for email (for display name, etc.)
            user_info = _resolve_member_identifier(member_id, slack_client, people_cache, people_json)
        else:
            # Assume it's a user ID, get user info
            user_info = slack_client.get_user_info(member_id)
            if user_info and user_info.get("email"):
                email = user_info["email"].lower()
        
        if not email or not validate_email(email):
            logger.warning(f"Invalid email format or could not resolve member: {sanitize_string_for_logging(member_id)}. Skipping.")
            continue

        # Skip if user has opted out of being shared with
        if email in no_share_set:
            logger.debug(f"User {sanitize_string_for_logging(email)} has opted out of being shared with, skipping")
            excluded_count += 1
            continue

        # Check if member should be shared with based on shareMembers list
        if not _should_share_with_member(member_id, user_info, share_members):
            display_name = user_info.get("displayName", member_id) if user_info else member_id
            logger.debug(
                f"User {email} ({display_name}) not in shareMembers list, skipping"
            )
            excluded_count += 1
            continue

        if email not in shared_emails:
            try:
                # Check if user has opted out of notifications
                send_notification = email not in no_notifications_set
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
                logger.debug(f"Error sharing folder with {sanitize_string_for_logging(email)}: {e}", exc_info=True)
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


# Backward compatibility aliases
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
    people_cache: Optional[Dict[str, str]] = None,
    people_json: Optional[Dict[str, Any]] = None,
) -> None:
    """Share a Google Drive folder with channel members and manage permissions.

    This is a backward-compatibility wrapper for share_folder_with_conversation_members().
    """
    share_folder_with_conversation_members(
        google_drive_client=google_drive_client,
        folder_id=folder_id,
        slack_client=slack_client,
        conversation_id=channel_id,
        conversation_name=channel_name,
        conversation_info=channel_info,
        no_notifications_set=no_notifications_set,
        no_share_set=no_share_set,
        stats=stats,
        config_source="channels.json",
        people_cache=people_cache,
        people_json=people_json,
    )


def share_folder_for_browser_export(
    google_drive_client: GoogleDriveClient,
    folder_id: str,
    slack_client: SlackClient,
    conversation_info: Dict[str, Any],
    conversation_name: str,
    no_notifications_set: set,
    no_share_set: set,
    stats: Dict[str, int],
    people_cache: Optional[Dict[str, str]] = None,
    people_json: Optional[Dict[str, Any]] = None,
) -> None:
    """Share a Google Drive folder for browser export using the same logic as Slack export.

    This is a backward-compatibility wrapper for share_folder_with_conversation_members().
    """
    from src.utils import setup_logging, sanitize_string_for_logging
    logger = setup_logging()
    
    conversation_id = conversation_info.get("id")
    if not conversation_id:
        logger.warning(f"No conversation ID found for {sanitize_string_for_logging(conversation_name)}. Cannot share.")
        return
    
    share_folder_with_conversation_members(
        google_drive_client=google_drive_client,
        folder_id=folder_id,
        slack_client=slack_client,
        conversation_id=conversation_id,
        conversation_name=conversation_name,
        conversation_info=conversation_info,
        no_notifications_set=no_notifications_set,
        no_share_set=no_share_set,
        stats=stats,
        config_source=BROWSER_EXPORT_CONFIG_FILENAME,
        people_cache=people_cache,
        people_json=people_json,
    )


def load_people_cache() -> Tuple[Dict[str, str], Set[str], Set[str], Optional[Dict[str, Any]]]:
    """Load people.json cache and opt-out sets.

    Returns:
        Tuple of (people_cache dict, no_notifications_set, no_share_set, people_json)
    """
    from src.utils import setup_logging, validate_people_json
    logger = setup_logging()
    
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
            people_json = None  # Don't use invalid JSON
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
        people_json = None
    return people_cache, no_notifications_set, no_share_set, people_json


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
    from src.utils import setup_logging, convert_date_to_timestamp, sanitize_filename
    logger = setup_logging()
    
    oldest_ts = None
    explicit_start_ts = None

    # Parse explicit start date if provided
    if explicit_start_date:
        try:
            explicit_start_ts = convert_date_to_timestamp(explicit_start_date)
            if explicit_start_ts is None:
                logger.error(f"Invalid start date format: {explicit_start_date}")
                return None
            logger.info(f"Explicit start date provided: {explicit_start_date} ({explicit_start_ts})")
        except Exception as e:
            logger.error(f"Error parsing explicit start date '{explicit_start_date}': {e}", exc_info=True)
            return None

    # Check Google Drive for last export timestamp if uploading to Drive
    if upload_to_drive and google_drive_client:
        # Create or get folder if we don't have folder_id yet
        if not folder_id and sanitized_folder_name:
            try:
                folder_id = google_drive_client.create_folder(
                    sanitized_folder_name, None  # Will use default parent folder
                )
            except Exception as e:
                logger.warning(
                    f"Failed to create/get folder '{sanitized_folder_name}' for timestamp lookup: {e}. "
                    f"Falling back to explicit start date if provided.",
                    exc_info=True
                )
                folder_id = None

        if folder_id:
            # Use safe_conversation_name if provided, otherwise sanitize conversation_name
            if not safe_conversation_name:
                safe_conversation_name = sanitize_filename(conversation_name)

            try:
                last_export_ts = google_drive_client.get_latest_export_timestamp(
                    folder_id, safe_conversation_name
                )
            except Exception as e:
                logger.warning(
                    f"Failed to get latest export timestamp from Drive for '{conversation_name}': {e}. "
                    f"Falling back to explicit start date if provided.",
                    exc_info=True
                )
                last_export_ts = None

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


def _validate_upload_params(
    messages: List[Dict[str, Any]], stats: Optional[Dict[str, int]]
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """Validate messages and initialize statistics dictionary.
    
    Args:
        messages: List of message dictionaries to validate
        stats: Optional statistics dictionary to initialize
        
    Returns:
        Tuple of (valid_messages, stats)
    """
    from src.utils import setup_logging
    logger = setup_logging()
    
    if stats is None:
        stats = {
            "processed": 0,
            "uploaded": 0,
            "upload_failed": 0,
            "total_messages": 0,
        }
    # Ensure all required keys exist (for consistency)
    for key in ["processed", "uploaded", "upload_failed", "total_messages"]:
        if key not in stats:
            stats[key] = 0

    # Validate messages before processing
    invalid_count = 0
    valid_messages = []
    for msg in messages:
        if validate_message(msg):
            valid_messages.append(msg)
        else:
            invalid_count += 1
            logger.debug(f"Skipping invalid message (missing required fields): {msg.get('ts', 'no timestamp')}")
    
    if invalid_count > 0:
        logger.warning(f"Skipped {invalid_count} invalid message(s) out of {len(messages)} total")
    
    return valid_messages, stats


def _check_doc_exists(
    google_drive_client: GoogleDriveClient, doc_name: str, folder_id: str
) -> bool:
    """Check if a Google Doc already exists in the folder.
    
    Args:
        google_drive_client: GoogleDriveClient instance
        doc_name: Name of the document to check
        folder_id: Google Drive folder ID
        
    Returns:
        True if document exists, False otherwise
    """
    from src.utils import setup_logging
    logger = setup_logging()
    
    escaped_doc_name = google_drive_client._escape_drive_query_string(doc_name)
    escaped_folder_id = google_drive_client._escape_drive_query_string(folder_id)
    query = (
        f"name='{escaped_doc_name}' and '{escaped_folder_id}' in parents "
        f"and mimeType='application/vnd.google-apps.document' and trashed=false"
    )

    try:
        google_drive_client._rate_limit()
        results = (
            google_drive_client.service.files()
            .list(q=query, fields="files(id, name, modifiedTime)", pageSize=100)
            .execute()
        )
        existing_files = results.get("files", [])
        if existing_files:
            if len(existing_files) > 1:
                logger.warning(
                    f"Found {len(existing_files)} documents with name '{doc_name}'. "
                    f"create_or_update_google_doc() will use the most recently modified."
                )
            return True
    except Exception as e:
        logger.debug(
            f"Error checking for existing doc '{doc_name}': {e}, assuming new doc",
            exc_info=True
        )
    return False


def _create_metadata_header(
    conversation_name: str,
    conversation_id: Optional[str],
    date_key: str,
    total_messages: int,
) -> str:
    """Create metadata header for new Google Docs.
    
    Args:
        conversation_name: Display name of the conversation
        conversation_id: Slack conversation ID (None for browser exports)
        date_key: Date key in YYYYMMDD format
        total_messages: Total number of messages for this day
        
    Returns:
        Metadata header string
    """
    export_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    date_obj = datetime.strptime(date_key, "%Y%m%d").replace(tzinfo=timezone.utc)
    date_display = date_obj.strftime("%Y-%m-%d")
    
    # Format channel ID for metadata header
    channel_id_display = conversation_id if conversation_id else "[Browser Export - No ID]"
    
    return f"""Slack Conversation Export
Channel: {conversation_name}
Channel ID: {channel_id_display}
Export Date: {export_date}
Date: {date_display}
Total Messages: {total_messages}

{'='*80}

"""


def _upload_message_chunk(
    google_drive_client: GoogleDriveClient,
    doc_name: str,
    folder_id: str,
    message_chunk: List[Dict[str, Any]],
    processed_messages: str,
    conversation_name: str,
    conversation_id: Optional[str],
    date_key: str,
    chunk_idx: int,
    total_chunks: int,
    daily_messages_count: int,
    doc_exists: bool,
    is_first_chunk: bool,
    stats: Dict[str, int],
) -> None:
    """Upload a single chunk of messages to Google Drive.
    
    Args:
        google_drive_client: GoogleDriveClient instance
        doc_name: Name of the Google Doc
        folder_id: Google Drive folder ID
        message_chunk: List of message dictionaries for this chunk
        processed_messages: Processed message text for this chunk
        conversation_name: Display name of the conversation
        conversation_id: Slack conversation ID (None for browser exports)
        date_key: Date key in YYYYMMDD format
        chunk_idx: Current chunk index (1-based)
        total_chunks: Total number of chunks for this day
        daily_messages_count: Total messages for this day
        doc_exists: Whether the document already exists
        is_first_chunk: Whether this is the first chunk
        stats: Statistics dictionary to update
    """
    from src.utils import setup_logging, sanitize_string_for_logging
    logger = setup_logging()
    
    chunk_info = (
        f" (chunk {chunk_idx}/{total_chunks})"
        if total_chunks > 1
        else ""
    )
    
    # Check for empty history after processing
    if not processed_messages or not processed_messages.strip():
        logger.warning(
            f"No processable content found for {date_key}{chunk_info} of {conversation_name}. Skipping."
        )
        return

    # Prepare content: add header only for first chunk of new docs
    if doc_exists or not is_first_chunk:
        # Append separator and messages (no header for existing docs or subsequent chunks)
        if total_chunks > 1:
            content_to_add = f"\n\n--- Chunk {chunk_idx} of {total_chunks} ({len(message_chunk)} messages) ---\n\n{processed_messages}"
        else:
            content_to_add = f"\n\n{'='*80}\n\n{processed_messages}"
    else:
        # Add full header for first chunk of new docs
        metadata_header = _create_metadata_header(
            conversation_name, conversation_id, date_key, daily_messages_count
        )
        content_to_add = metadata_header + processed_messages

    # Create or update Google Doc (append mode for incremental updates)
    doc_id = google_drive_client.create_or_update_google_doc(
        doc_name, content_to_add, folder_id, overwrite=False
    )

    if not doc_id:
        logger.error(
            f"Failed to create Google Doc for {date_key}{chunk_info} of {sanitize_string_for_logging(conversation_name)} "
            f"(folder_id: {folder_id}, doc_name: {sanitize_string_for_logging(doc_name)})"
        )
        stats["upload_failed"] += 1
    else:
        if is_first_chunk:
            stats["uploaded"] += 1
            stats["processed"] += 1
        stats["total_messages"] += len(message_chunk)
        logger.info(f"Created/updated Google Doc for {date_key}{chunk_info}")


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
    from src.utils import setup_logging, sanitize_string_for_logging, sanitize_filename
    logger = setup_logging()
    
    # Validate messages and initialize stats
    valid_messages, stats = _validate_upload_params(messages, stats)
    
    if not valid_messages:
        logger.warning("No valid messages found to upload")
        return stats

    # Group messages by date
    daily_groups = group_messages_by_date(valid_messages)
    logger.info(
        f"Grouped {len(valid_messages)} messages into {len(daily_groups)} daily group(s)"
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
        logger.error(
            f"Failed to create/get folder for {sanitize_string_for_logging(conversation_name)} "
            f"(parent_folder_id: {google_drive_folder_id}, sanitized_name: {sanitized_folder_name})"
        )
        return stats

    logger.info(f"Using folder: {sanitized_folder_name} ({folder_id})")

    # Sort dates chronologically
    sorted_dates = sorted(daily_groups.keys())

    for date_key in sorted_dates:
        daily_messages = daily_groups[date_key]
        logger.info(f"Processing {len(daily_messages)} messages for date {date_key}")

        # Memory management: chunk large daily message groups
        if len(daily_messages) > DAILY_MESSAGE_CHUNK_SIZE:
            logger.info(
                f"Large daily message group detected ({len(daily_messages)} messages). "
                f"Processing in chunks of {DAILY_MESSAGE_CHUNK_SIZE} to manage memory."
            )
            message_chunks = [
                daily_messages[i : i + DAILY_MESSAGE_CHUNK_SIZE]
                for i in range(0, len(daily_messages), DAILY_MESSAGE_CHUNK_SIZE)
            ]
        else:
            message_chunks = [daily_messages]

        # Create doc name: conversation name slack messages yyyymmdd
        doc_name_base = f"{conversation_name} slack messages {date_key}"
        doc_name = sanitize_folder_name(doc_name_base)

        # Check if doc already exists to determine if we need a header
        doc_exists = _check_doc_exists(google_drive_client, doc_name, folder_id)

        # Process each chunk for this day
        is_first_chunk = True
        for chunk_idx, message_chunk in enumerate(message_chunks, 1):
            chunk_info = (
                f" (chunk {chunk_idx}/{len(message_chunks)})"
                if len(message_chunks) > 1
                else ""
            )
            logger.info(
                f"Processing {len(message_chunk)} messages for date {date_key}{chunk_info}"
            )

            # Process messages for this chunk
            if use_display_names:
                processed_messages = preprocess_history(
                    message_chunk, slack_client=None, people_cache=None, use_display_names=True
                )
            else:
                processed_messages = preprocess_history(
                    message_chunk, slack_client, people_cache
                )

            # Upload chunk using helper function
            _upload_message_chunk(
                google_drive_client=google_drive_client,
                doc_name=doc_name,
                folder_id=folder_id,
                message_chunk=message_chunk,
                processed_messages=processed_messages,
                conversation_name=conversation_name,
                conversation_id=conversation_id,
                date_key=date_key,
                chunk_idx=chunk_idx,
                total_chunks=len(message_chunks),
                daily_messages_count=len(daily_messages),
                doc_exists=doc_exists,
                is_first_chunk=is_first_chunk,
                stats=stats,
            )

            is_first_chunk = False

    # Save export metadata with latest timestamp from all messages
    if valid_messages:
        latest_message_ts = max(float(msg.get("ts", 0)) for msg in valid_messages)
        safe_conversation_name = sanitize_filename(conversation_name)
        google_drive_client.save_export_metadata(
            folder_id, safe_conversation_name, str(latest_message_ts)
        )
        logger.info(f"Saved export metadata for {conversation_name}")

    return stats


def initialize_stats() -> Dict[str, int]:
    """Initialize statistics dictionary with default values.

    Returns:
        Statistics dictionary with all counters initialized to 0
    """
    return {
        "processed": 0,
        "skipped": 0,
        "failed": 0,
        "uploaded": 0,
        "upload_failed": 0,
        "shared": 0,
        "share_failed": 0,
        "total_messages": 0,
    }


def log_statistics(stats: Dict[str, int], upload_to_drive: bool) -> None:
    """Log export statistics.

    Args:
        stats: Statistics dictionary
        upload_to_drive: Whether Drive upload was enabled
    """
    from src.utils import setup_logging
    logger = setup_logging()
    
    logger.info("=" * 80)
    logger.info("Export Statistics:")
    logger.info(f"  Processed: {stats.get('processed', 0)}")
    logger.info(f"  Skipped: {stats.get('skipped', 0)}")
    logger.info(f"  Failed: {stats.get('failed', 0)}")
    if upload_to_drive:
        logger.info(f"  Uploaded to Drive: {stats.get('uploaded', 0)}")
        logger.info(f"  Upload Failed: {stats.get('upload_failed', 0)}")
        if 'shared' in stats:
            logger.info(f"  Folders shared: {stats.get('shared', 0)}")
            logger.info(f"  Share Failed: {stats.get('share_failed', 0)}")
    logger.info(f"  Total messages processed: {stats.get('total_messages', 0)}")
    logger.info("=" * 80)
