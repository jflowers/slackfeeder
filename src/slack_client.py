import logging
import time
from typing import Dict, List, Optional

from cachetools import LRUCache
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from src.utils import save_json_file

logger = logging.getLogger(__name__)

# Constants for API pagination and rate limiting
DEFAULT_PAGE_SIZE = 200
CONVERSATIONS_PAGE_SIZE = 100
DEFAULT_RATE_LIMIT_DELAY = 1.2  # seconds
MAX_RETRIES = 3
BASE_RETRY_DELAY = 1.2  # seconds
SHARE_RATE_LIMIT_INTERVAL = 10  # shares per interval
SHARE_RATE_LIMIT_DELAY = 1.0  # seconds between intervals
API_TIMEOUT_SECONDS = 30  # seconds
MAX_RETRY_DELAY_SECONDS = 60  # seconds
MAX_USER_CACHE_SIZE = 10000  # Maximum number of users to cache


class SlackClient:
    def __init__(self, token: str):
        """Initialize Slack client.

        Args:
            token: Slack bot token (starts with xoxb-)

        Raises:
            ValueError: If token is missing or invalid
        """
        if not token or token == "xoxb-your-token-here":
            raise ValueError(
                "Slack Bot Token is missing or not replaced. Please set the SLACK_BOT_TOKEN."
            )
        # Validate token format
        if not token.startswith("xoxb-") and not token.startswith("xoxp-"):
            # Don't expose actual token characters in error message
            token_prefix = (
                "xoxb-"
                if token.startswith("xoxb")
                else ("xoxp-" if token.startswith("xoxp") else "unknown")
            )
            raise ValueError(
                f"Invalid Slack token format. Expected token starting with 'xoxb-' or 'xoxp-', got: {token_prefix}..."
            )
        self.client = WebClient(token=token, timeout=API_TIMEOUT_SECONDS)
        # Use LRU cache to prevent unbounded memory growth
        self.user_cache: LRUCache[str, Optional[Dict[str, str]]] = LRUCache(
            maxsize=MAX_USER_CACHE_SIZE
        )

    def _handle_slack_api_error(self, error: SlackApiError, context: str) -> str:
        """Centralized Slack API error handling.

        Args:
            error: SlackApiError exception
            context: Context description for logging

        Returns:
            Error code string
        """
        error_code = (
            error.response.get("error", "unknown") if hasattr(error, "response") else "unknown"
        )
        logger.error(f"Slack API error {context}: {error_code}")
        return error_code

    def get_user_info(self, user_id: str) -> Optional[Dict[str, str]]:
        """Fetches user info and formats it.

        Args:
            user_id: Slack user ID

        Returns:
            Dict with slackId, email, displayName, or None if error/bot
        """
        if user_id in self.user_cache:
            return self.user_cache[user_id]

        try:
            response = self.client.users_info(user=user_id)
            user = response.get("user")

            if not user:
                logger.warning(f"No user data returned for {user_id}")
                self.user_cache[user_id] = None
                return None

            if user.get("is_bot"):
                logger.info(f"Skipping bot user: {user.get('name')}")
                self.user_cache[user_id] = None
                return None

            profile = user.get("profile", {})

            display_name = profile.get("display_name_normalized")
            if not display_name:
                display_name = profile.get("real_name_normalized", user.get("name", "Unknown User"))

            email = profile.get("email")

            if not email:
                logger.warning(
                    f"Could not find email for {display_name} (ID: {user_id}). Bot may be missing 'users:read.email' scope."
                )

            user_data = {
                "slackId": user["id"],
                "email": email,
                "displayName": display_name,
            }

            self.user_cache[user_id] = user_data
            return user_data

        except SlackApiError as e:
            self._handle_slack_api_error(e, f"fetching info for user {user_id}")
            self.user_cache[user_id] = None
            return None
        except (KeyError, AttributeError) as e:
            logger.error(f"Unexpected response format for user {user_id}: {e}")
            self.user_cache[user_id] = None
            return None

    def get_channel_members(self, channel_id: str) -> List[str]:
        """Fetches all member IDs for a channel, handling pagination.

        Args:
            channel_id: Slack channel ID

        Returns:
            List of member user IDs
        """
        member_ids = []
        cursor = None
        while True:
            try:
                response = self.client.conversations_members(
                    channel=channel_id, limit=DEFAULT_PAGE_SIZE, cursor=cursor
                )
                member_ids.extend(response.get("members", []))
                cursor = response.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break
            except SlackApiError as e:
                self._handle_slack_api_error(e, f"getting members for channel {channel_id}")
                logger.warning(f"Ensure the bot is a member of this channel.")
                return []
        return member_ids

    def get_all_channels(self):
        """Fetches all channels and group chats the bot is a member of.

        Excludes direct messages (DMs) between the bot and individuals.
        Only includes:
        - Public channels
        - Private channels
        - Group DMs (mpim)

        Returns:
            List of conversation objects with detailed info
        """
        all_channels_list = []
        channels_cursor = None
        logger.info("Starting to fetch conversations the bot is a member of (excluding DMs)...")

        while True:
            try:
                response = self.client.users_conversations(
                    types="public_channel,private_channel,mpim",
                    limit=CONVERSATIONS_PAGE_SIZE,
                    cursor=channels_cursor,
                )

                channels = response.get("channels", [])

                failures = []
                for channel_summary in channels:
                    if channel_summary.get("is_archived"):
                        logger.info(
                            f"Skipping archived conversation: {channel_summary.get('name', channel_summary.get('id'))}"
                        )
                        continue

                    # Validate channel_summary has ID
                    channel_id = channel_summary.get("id")
                    if not channel_id:
                        logger.warning(f"Skipping channel summary without ID: {channel_summary}")
                        failures.append("unknown")
                        continue

                    # Fetch detailed info for each conversation
                    try:
                        channel_info_response = self.client.conversations_info(channel=channel_id)
                        channel_detail = channel_info_response.get("channel", {})

                        # Skip direct messages (DMs) - safety check in case any slip through
                        if channel_detail.get("is_im"):
                            logger.debug(f"Skipping direct message: {channel_id}")
                            continue

                        # Construct a more detailed channel object
                        channel_obj = {
                            "id": channel_detail.get("id"),
                            "name": channel_detail.get("name"),
                            "is_im": channel_detail.get("is_im"),
                            "is_mpim": channel_detail.get("is_mpim"),
                            "user": channel_detail.get("user"),
                            "members": channel_detail.get("members", []),
                        }
                        all_channels_list.append(channel_obj)

                    except SlackApiError as e:
                        error_code = self._handle_slack_api_error(
                            e, f"fetching details for conversation {channel_id}"
                        )
                        failures.append(channel_id)
                        logger.error(
                            f"Could not fetch details for conversation {channel_id}: {error_code}"
                        )

                if failures:
                    failure_rate = len(failures) / len(channels) if channels else 0
                    if failure_rate > 0.5:  # More than 50% failed
                        logger.error(
                            f"High failure rate ({failure_rate:.1%}). Only {len(all_channels_list)}/{len(channels)} conversations fetched successfully."
                        )
                    else:
                        logger.warning(
                            f"Failed to fetch details for {len(failures)} out of {len(channels)} conversations"
                        )

                channels_cursor = response.get("response_metadata", {}).get("next_cursor")
                if not channels_cursor:
                    logger.info("Fetched all pages of conversations.")
                    break

            except SlackApiError as e:
                error_code = e.response.get("error", "unknown")
                logger.error(f"Error fetching conversation list: {error_code}")
                break
            except (KeyError, AttributeError) as e:
                logger.error(f"Unexpected response format: {e}")
                break

        return all_channels_list

    def fetch_channel_history(
        self, channel_id: str, oldest_ts: Optional[str] = None, latest_ts: Optional[str] = None
    ) -> Optional[List[Dict]]:
        """Fetches the message history for a given Slack channel.

        Args:
            channel_id: Slack channel ID
            oldest_ts: Optional oldest timestamp (Unix timestamp string)
            latest_ts: Optional latest timestamp (Unix timestamp string)

        Returns:
            List of message objects sorted by timestamp, or None on error
        """
        all_messages = []
        next_cursor = None
        page_count = 0
        retry_count = 0  # Track retries per rate limit event

        logger.info(f"Starting message export for channel: {channel_id}")

        try:
            while True:
                page_count += 1
                logger.info(f"Fetching page {page_count} for channel {channel_id}...")
                try:
                    response = self.client.conversations_history(
                        channel=channel_id,
                        limit=DEFAULT_PAGE_SIZE,
                        cursor=next_cursor,
                        oldest=oldest_ts,
                        latest=latest_ts,
                    )
                    # Reset retry count only on successful (non-rate-limited) response
                    retry_count = 0

                except SlackApiError as e:
                    error_code = (
                        e.response.get("error", "unknown") if hasattr(e, "response") else "unknown"
                    )
                    http_status = (
                        getattr(e.response, "status_code", None) if hasattr(e, "response") else None
                    )
                    logger.error(
                        f"Slack API Error for channel {channel_id} (Page {page_count}): {error_code} (HTTP {http_status})"
                    )

                    # Handle rate limiting
                    if error_code == "ratelimited" and retry_count < MAX_RETRIES:
                        retry_count += 1
                        try:
                            retry_after = int(
                                e.response.headers.get(
                                    "Retry-After", BASE_RETRY_DELAY * (2**retry_count)
                                )
                            )
                            # Bound the retry delay (max 60 seconds)
                            retry_after = min(retry_after, MAX_RETRY_DELAY_SECONDS)
                        except (ValueError, TypeError):
                            retry_after = BASE_RETRY_DELAY * (2**retry_count)
                        logger.warning(
                            f"Rate limited. Retrying after {retry_after} seconds... (Attempt {retry_count}/{MAX_RETRIES})"
                        )
                        time.sleep(retry_after)
                        page_count -= 1  # Don't increment page count for retry
                        continue
                    # Handle transient errors (5xx, timeouts) with exponential backoff
                    elif http_status and http_status >= 500 and retry_count < MAX_RETRIES:
                        retry_count += 1
                        retry_after = BASE_RETRY_DELAY * (2**retry_count)
                        retry_after = min(retry_after, MAX_RETRY_DELAY_SECONDS)
                        logger.warning(
                            f"Transient error (HTTP {http_status}). Retrying after {retry_after} seconds... (Attempt {retry_count}/{MAX_RETRIES})"
                        )
                        time.sleep(retry_after)
                        page_count -= 1  # Don't increment page count for retry
                        continue
                    else:
                        logger.error(
                            f"Stopping export for channel {channel_id} due to API error: {error_code}"
                        )
                        return None
                except Exception as e:
                    # Handle network timeouts and other transient exceptions
                    logger.debug(f"Transient error during API call: {e}", exc_info=True)
                    error_str = str(e).lower()
                    is_transient = any(
                        keyword in error_str
                        for keyword in ["timeout", "connection", "network", "temporary"]
                    )

                    if is_transient and retry_count < MAX_RETRIES:
                        retry_count += 1
                        retry_after = BASE_RETRY_DELAY * (2**retry_count)
                        retry_after = min(retry_after, MAX_RETRY_DELAY_SECONDS)
                        logger.warning(
                            f"Transient error ({str(e)}). Retrying after {retry_after} seconds... (Attempt {retry_count}/{MAX_RETRIES})"
                        )
                        time.sleep(retry_after)
                        page_count -= 1  # Don't increment page count for retry
                        continue
                    else:
                        logger.error(
                            f"An unexpected error occurred during API call for channel {channel_id}: {e}",
                            exc_info=True,
                        )
                        return None

                # Parse response - handle potential KeyError/AttributeError from response structure
                try:
                    messages = response.get("messages", [])
                    all_messages.extend(messages)
                    logger.info(
                        f"Fetched {len(messages)} messages on page {page_count} for channel {channel_id}. Total fetched: {len(all_messages)}"
                    )

                    response_metadata = response.get("response_metadata")
                    if response_metadata:
                        next_cursor = response_metadata.get("next_cursor")
                    else:
                        next_cursor = None
                except (KeyError, AttributeError) as e:
                    logger.error(f"Unexpected response format for channel {channel_id}: {e}")
                    return None

                if not next_cursor:
                    logger.info(f"Reached the end of the message history for channel {channel_id}.")
                    break

                time.sleep(DEFAULT_RATE_LIMIT_DELAY)

        except (KeyError, AttributeError) as e:
            logger.error(f"Unexpected error in pagination loop for channel {channel_id}: {e}")
            return None
        except Exception as e:
            logger.error(
                f"An unexpected error occurred during pagination loop for channel {channel_id}: {e}",
                exc_info=True,
            )
            return None

        all_messages.sort(key=lambda x: float(x.get("ts", 0)))
        return all_messages
