import logging
import time
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from src.utils import save_json_file

logger = logging.getLogger(__name__)

class SlackClient:
    def __init__(self, token):
        if not token or token == "xoxb-your-token-here":
            raise ValueError("Slack Bot Token is missing or not replaced. Please set the SLACK_BOT_TOKEN.")
        self.client = WebClient(token=token)
        self.user_cache = {}

    def get_user_info(self, user_id):
        """Fetches user info and formats it."""
        if user_id in self.user_cache:
            return self.user_cache[user_id]
            
        try:
            response = self.client.users_info(user=user_id)
            user = response["user"]

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
            logger.error(f"Error fetching info for user {user_id}: {e.response['error']}")
            self.user_cache[user_id] = None
            return None

    def get_channel_members(self, channel_id):
        """Fetches all member IDs for a channel, handling pagination."""
        member_ids = []
        cursor = None
        while True:
            try:
                response = self.client.conversations_members(
                    channel=channel_id,
                    limit=200,
                    cursor=cursor
                )
                member_ids.extend(response["members"])
                cursor = response.get("response_metadata", {}).get("next_cursor")
                if not cursor:
                    break
            except SlackApiError as e:
                logger.error(f"Error getting members for channel {channel_id}: {e.response['error']}")
                logger.warning(f"Ensure the bot is a member of this channel.")
                return []
        return member_ids

    def get_all_channels(self):
        """Fetches all channels the bot is a member of (including DMs and group chats)."""
        all_channels_list = []
        channels_cursor = None
        logger.info("Starting to fetch conversations the bot is a member of...")

        while True:
            try:
                response = self.client.users_conversations(
                    types="public_channel,private_channel,mpim,im",
                    limit=100,
                    cursor=channels_cursor
                )
                
                channels = response["channels"]

                for channel in channels:
                    if channel.get("is_archived"):
                        logger.info(f"Skipping archived conversation: {channel.get('name', channel.get('id'))}")
                        continue
                    all_channels_list.append(channel)

                channels_cursor = response.get("response_metadata", {}).get("next_cursor")
                if not channels_cursor:
                    logger.info("Fetched all pages of conversations.")
                    break
                    
            except SlackApiError as e:
                logger.error(f"Error fetching conversation list: {e.response['error']}")
                break
        
        return all_channels_list

    def fetch_channel_history(self, channel_id, oldest_ts=None, latest_ts=None):
        """Fetches the message history for a given Slack channel."""
        all_messages = []
        next_cursor = None
        page_count = 0
        retry_count = 0
        max_retries = 3

        logger.info(f"Starting message export for channel: {channel_id}")

        try:
            while True:
                page_count += 1
                logger.info(f"Fetching page {page_count} for channel {channel_id}...")
                try:
                    response = self.client.conversations_history(
                        channel=channel_id,
                        limit=200,
                        cursor=next_cursor,
                        oldest=oldest_ts,
                        latest=latest_ts
                    )
                    retry_count = 0

                except SlackApiError as e:
                    logger.error(f"Slack API Error for channel {channel_id} (Page {page_count}): {e.response['error']}")
                    if e.response['error'] == 'ratelimited' and retry_count < max_retries:
                        retry_count += 1
                        retry_after = int(e.response.headers.get('Retry-After', 1.2 * (2**retry_count)))
                        logger.warning(f"Rate limited. Retrying after {retry_after} seconds... (Attempt {retry_count}/{max_retries})")
                        time.sleep(retry_after)
                        page_count -= 1
                        continue
                    else:
                        logger.error(f"Stopping export for channel {channel_id} due to unhandled API error: {e.response['error']}")
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

                time.sleep(1.2)

        except Exception as e:
            logger.error(f"An unexpected error occurred during pagination loop for channel {channel_id}: {e}", exc_info=True)
            return None

        all_messages.sort(key=lambda x: float(x.get('ts', 0)))
        return all_messages
