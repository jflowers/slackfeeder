import logging
import time
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

class SlackClient:
    def __init__(self, token):
        if not token or token == "xoxb-your-token-here":
            raise ValueError("Slack Bot Token is missing or not replaced. Please set the SLACK_BOT_TOKEN.")
        self.client = WebClient(token=token)
        self.user_cache = {}

    def get_user_info(self, user_id):
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
                logger.warning(f"Could not find email for {display_name} (ID: {user_id}). Bot may be missing 'users:read.email' scope.")

            user_data = {
                "slackId": user["id"],
                "email": email,
                "displayName": display_name,
            }
            
            self.user_cache[user_id] = user_data
            return user_data

        except SlackApiError as e:
            error_code = e.response.get('error', 'unknown') if hasattr(e, 'response') else 'unknown'
            logger.error(f"Error fetching info for user {user_id}: {error_code}")
            self.user_cache[user_id] = None
            return None
        except (KeyError, AttributeError) as e:
            logger.error(f"Unexpected response format for user {user_id}: {e}")
            self.user_cache[user_id] = None
            return None

    def get_channel_members(self, channel_id):
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
                    channel=channel_id,
                    limit=self.DEFAULT_PAGE_SIZE,
                    cursor=cursor
                )
                member_ids.extend(response.get("members", []))
                cursor = response.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break
            except SlackApiError as e:
                error_code = e.response.get('error', 'unknown')
                logger.error(f"Error getting members for channel {channel_id}: {error_code}")
                logger.warning(f"Ensure the bot is a member of this channel.")
                return []
        return member_ids

    def get_all_channels(self):
        """Fetches all channels the bot is a member of (including DMs and group chats).
        
        Returns:
            List of conversation objects with detailed info
        """
        all_channels_list = []
        channels_cursor = None
        logger.info("Starting to fetch conversations the bot is a member of...")

        while True:
            try:
                response = self.client.users_conversations(
                    types="public_channel,private_channel,mpim,im",
                    limit=CONVERSATIONS_PAGE_SIZE,
                    cursor=channels_cursor
                )
                
                channels = response.get("channels", [])

                for channel_summary in channels:
                    if channel_summary.get("is_archived"):
                        logger.info(f"Skipping archived conversation: {channel_summary.get('name', channel_summary.get('id'))}")
                        continue
                    
                    # Fetch detailed info for each conversation
                    try:
                        channel_info_response = self.client.conversations_info(channel=channel_summary['id'])
                        channel_detail = channel_info_response.get("channel", {})
                        
                        # Construct a more detailed channel object
                        channel_obj = {
                            "id": channel_detail.get("id"),
                            "name": channel_detail.get("name"),
                            "is_im": channel_detail.get("is_im"),
                            "is_mpim": channel_detail.get("is_mpim"),
                            "user": channel_detail.get("user"),
                            "members": channel_detail.get("members", [])
                        }
                        all_channels_list.append(channel_obj)
                        
                    except SlackApiError as e:
                        logger.error(f"Could not fetch details for conversation {channel_summary.get('id')}: {e.response['error']}")

                channels_cursor = response.get("response_metadata", {}).get("next_cursor")
                if not channels_cursor:
                    logger.info("Fetched all pages of conversations.")
                    break
                    
            except SlackApiError as e:
                error_code = e.response.get('error', 'unknown')
                logger.error(f"Error fetching conversation list: {error_code}")
                break
            except (KeyError, AttributeError) as e:
                logger.error(f"Unexpected response format: {e}")
                break
        
        return all_channels_list

    def fetch_channel_history(self, channel_id, oldest_ts=None, latest_ts=None):
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
        retry_count = 0

        logger.info(f"Starting message export for channel: {channel_id}")

        try:
            while True:
                page_count += 1
                logger.info(f"Fetching page {page_count} for channel {channel_id}...")
                try:
                    response = self.client.conversations_history(
                        channel=channel_id,
                        limit=self.DEFAULT_PAGE_SIZE,
                        cursor=next_cursor,
                        oldest=oldest_ts,
                        latest=latest_ts
                    )
                    retry_count = 0

                except SlackApiError as e:
                    error_code = e.response.get('error', 'unknown')
                    logger.error(f"Slack API Error for channel {channel_id} (Page {page_count}): {error_code}")
                    
                    if error_code == 'ratelimited' and retry_count < self.MAX_RETRIES:
                        retry_count += 1
                        retry_after = int(e.response.headers.get('Retry-After', self.BASE_RETRY_DELAY * (2**retry_count)))
                        logger.warning(f"Rate limited. Retrying after {retry_after} seconds... (Attempt {retry_count}/{self.MAX_RETRIES})")
                        time.sleep(retry_after)
                        page_count -= 1
                        continue
                    else:
                        logger.error(f"Stopping export for channel {channel_id} due to API error: {error_code}")
                        return None

                except (KeyError, AttributeError) as e:
                    logger.error(f"Unexpected response format for channel {channel_id}: {e}")
                    return None
                except Exception as e:
                    logger.error(f"An unexpected error occurred during API call for channel {channel_id}: {e}", exc_info=True)
                    return None

                messages = response.get("messages", [])
                all_messages.extend(messages)
                logger.info(f"Fetched {len(messages)} messages on page {page_count} for channel {channel_id}. Total fetched: {len(all_messages)}")

                response_metadata = response.get("response_metadata")
                if response_metadata:
                    next_cursor = response_metadata.get("next_cursor")
                else:
                    next_cursor = None

                if not next_cursor:
                    logger.info(f"Reached the end of the message history for channel {channel_id}.")
                    break

                time.sleep(self.DEFAULT_RATE_LIMIT_DELAY)

        except (KeyError, AttributeError) as e:
            logger.error(f"Unexpected error in pagination loop for channel {channel_id}: {e}")
            return None
        except Exception as e:
            logger.error(f"An unexpected error occurred during pagination loop for channel {channel_id}: {e}", exc_info=True)
            return None

        all_messages.sort(key=lambda x: float(x.get('ts', 0)))
        return all_messages
