import os
import json
import logging
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

# --- Configuration ---
BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")
PEOPLE_FILENAME = "ref/people.json"
CHANNELS_FILENAME = "ref/channels.json"

# --- Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

if not BOT_TOKEN:
    logger.error("SLACK_BOT_TOKEN environment variable not set.")
    logger.error("Please set it before running the script.")
    exit(1)

client = WebClient(token=BOT_TOKEN)
# This cache avoids re-fetching user data if a user is in multiple channels
user_cache = {}

def get_jira_field(profile):
    """
    Attempts to find a custom profile field labeled 'Jira'.
    NOTE: This is case-insensitive, but the label must be 'Jira'.
    """
    custom_fields = profile.get("fields", {})
    if not custom_fields:
        return None
        
    for field in custom_fields.values():
        label = field.get("label", "").lower()
        if label == "jira":
            return field.get("value")
            
    return None

def get_user_info(user_id):
    """
    Fetches user info and formats it according to the people.json schema.
    Uses a cache to avoid redundant API calls.
    """
    if user_id in user_cache:
        return user_cache[user_id]
        
    try:
        response = client.users_info(user=user_id)
        user = response["user"]

        if user.get("is_bot"):
            logger.info(f"Skipping bot user: {user.get('name')}")
            user_cache[user_id] = None
            return None

        profile = user.get("profile", {})
        
        display_name = profile.get("display_name_normalized")
        if not display_name:
            display_name = profile.get("real_name_normalized", user.get("name", "Unknown User"))
            
        email = profile.get("email")
        jira_value = get_jira_field(profile)

        if not email:
            logger.warning(f"Could not find email for {display_name} (ID: {user_id}). Bot may be missing 'users:read.email' scope.")
        if not jira_value:
             logger.warning(f"Could not find 'Jira' custom field for {display_name} (ID: {user_id}).")

        user_data = {
            "slackId": user["id"],
            "email": email,
            "displayName": display_name,
            "jira": jira_value
        }
        
        user_cache[user_id] = user_data # Save to cache
        return user_data

    except SlackApiError as e:
        logger.error(f"Error fetching info for user {user_id}: {e.response['error']}")
        user_cache[user_id] = None
        return None

def get_channel_members(channel_id):
    """
    Fetches all member IDs for a channel, handling pagination.
    """
    member_ids = []
    cursor = None
    while True:
        try:
            response = client.conversations_members(
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

def main():
    """
    Main function to fetch channels *the bot is in* and users, then write to files.
    This version uses users.conversations for better efficiency.
    """
    all_channels_list = []
    # Use a dict for users to de-duplicate by user ID automatically
    all_users_dict = {} 
    
    channels_cursor = None
    logger.info("Starting to fetch channels the bot is a member of (using users.conversations)...")

    while True:
        try:
            # *** EFFICIENT METHOD ***
            # This call *only* returns channels the bot is a member of.
            response = client.users_conversations(
                types="public_channel,private_channel",
                limit=100,
                cursor=channels_cursor
            )
            
            channels = response["channels"]

            for channel in channels:
                channel_id = channel["id"]
                channel_name = channel["name"]
                
                # We no longer need the 'is_member' check here!
                # The API has already filtered for us.
                
                if channel.get("is_archived"):
                    logger.info(f"Skipping archived channel: #{channel_name}")
                    continue

                logger.info(f"--- Processing channel: #{channel_name} (ID: {channel_id}) ---")
                
                # Add channel to our channel list
                all_channels_list.append({
                    "id": channel_id,
                    "displayName": channel_name
                })
                
                # Get all member IDs for this channel to find users
                member_ids = get_channel_members(channel_id)
                if not member_ids:
                    logger.warning(f"No members found for #{channel_name}.")
                    continue

                logger.info(f"Found {len(member_ids)} members. Checking for new users...")
                
                # Get info for each member
                for user_id in member_ids:
                    # Only fetch if we haven't seen this user before
                    if user_id not in all_users_dict:
                        user_info = get_user_info(user_id)
                        if user_info: # Add to dict if not a bot and no errors
                            all_users_dict[user_id] = user_info

            # Check for pagination
            channels_cursor = response.get("response_metadata", {}).get("next_cursor")
            if not channels_cursor:
                logger.info("Fetched all pages of channels.")
                break
                
        except SlackApiError as e:
            logger.error(f"Error fetching channel list: {e.response['error']}")
            break

    # --- Write Files (This part is unchanged) ---

    # 1. Prepare and write channels.json
    channels_output = {"channels": all_channels_list}
    try:
        with open(CHANNELS_FILENAME, 'w', encoding='utf-8') as f:
            json.dump(channels_output, f, indent=4, ensure_ascii=False)
        logger.info(f"Successfully saved {len(all_channels_list)} channels to {CHANNELS_FILENAME}")
    except IOError as e:
        logger.error(f"Failed to write JSON to file {CHANNELS_FILENAME}: {e}")

    # 2. Prepare and write people.json
    people_output = {"people": list(all_users_dict.values())}
    try:
        with open(PEOPLE_FILENAME, 'w', encoding='utf-8') as f:
            json.dump(people_output, f, indent=4, ensure_ascii=False)
        logger.info(f"Successfully saved {len(all_users_dict)} unique users to {PEOPLE_FILENAME}")
    except IOError as e:
        logger.error(f"Failed to write JSON to file {PEOPLE_FILENAME}: {e}")

if __name__ == "__main__":
    main()