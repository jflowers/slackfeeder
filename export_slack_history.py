import os
import json
import logging
import time
from datetime import datetime, timezone
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
import sys # Import sys for exiting

# --- Configuration ---
# Replace with your Bot User OAuth Token (starts with xoxb-)
# It's recommended to use environment variables for security
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN")

# --- Path to the channels JSON file ---
CHANNELS_FILE = "ref/conversations.json" # Relative path to your JSON file

# --- Output Directory ---
# Files will be saved here, named like <channel_id>_history.json
OUTPUT_DIR = "slack_exports"

# Delay between requests (to respect rate limits)
REQUEST_DELAY_SECONDS = 1.2 # Slack Tier 2 allows ~50 requests/min

# --- Timespan Configuration (Optional) ---
# Set to None or "YYYY-MM-DD" or "YYYY-MM-DD HH:MM:SS" (assumed to be UTC)
# If None, no time limit is applied.
START_DATE_STR = os.environ.get("START_DATE_STR")  # e.g., "2024-01-01" (fetches messages *from* this time)
END_DATE_STR = os.environ.get("END_DATE_STR")    # e.g., "2024-01-31" (fetches messages *up to* this time)

# --- Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Helper Function ---
def load_channels_from_json(filepath):
    """Loads channel data from the specified JSON file."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if "channels" not in data or not isinstance(data["channels"], list):
            logger.error(f"Invalid format in {filepath}. Expected a JSON object with a 'channels' list.")
            return None
        # Basic validation of channel entries
        valid_channels = []
        for i, channel in enumerate(data["channels"]):
            if isinstance(channel, dict) and "id" in channel:
                valid_channels.append(channel)
            else:
                logger.warning(f"Skipping invalid channel entry at index {i} in {filepath}: {channel}")
        
        if not valid_channels:
             logger.error(f"No valid channel entries found in {filepath}.")
             return None
             
        logger.info(f"Loaded {len(valid_channels)} channels from {filepath}")
        return valid_channels
    except FileNotFoundError:
        logger.error(f"Channels file not found: {filepath}")
        return None
    except json.JSONDecodeError as e:
        logger.error(f"Error decoding JSON from {filepath}: {e}")
        return None
    except Exception as e:
        logger.error(f"An unexpected error occurred loading channels from {filepath}: {e}", exc_info=True)
        return None


# --- Main Script ---
def fetch_channel_history(token, channel_id, output_file, oldest_str=None, latest_str=None):
    """
    Fetches the message history for a given Slack channel using pagination
    and saves it to a JSON file. Can be limited by a timespan.

    Args:
        token (str): Your Slack Bot User OAuth Token.
        channel_id (str): The ID of the channel to fetch history from.
        output_file (str): The path to the output JSON file.
        oldest_str (str, optional): The start of the timespan (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS UTC).
        latest_str (str, optional): The end of the timespan (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS UTC).
    """
    if not token or token == "YOUR_SLACK_BOT_TOKEN_HERE":
        logger.error("Slack Bot Token is missing or not replaced. Please set the SLACK_BOT_TOKEN variable.")
        return False # Indicate failure

    # Basic check for channel_id validity (can be enhanced)
    if not channel_id or not isinstance(channel_id, str) or not channel_id.strip():
         logger.error(f"Invalid Channel ID provided: '{channel_id}'")
         return False # Indicate failure

    # Ensure output directory exists
    output_dir = os.path.dirname(output_file)
    if output_dir and not os.path.exists(output_dir):
        try:
            os.makedirs(output_dir)
            logger.info(f"Created output directory: {output_dir}")
        except OSError as e:
            logger.error(f"Failed to create output directory {output_dir}: {e}")
            return False # Indicate failure


    client = WebClient(token=token)

    # --- Timestamp Conversion ---
    def convert_date_to_timestamp(date_str, is_end_date=False):
        """Converts YYYY-MM-DD or YYYY-MM-DD HH:MM:SS string (assumed UTC) to Unix timestamp string."""
        if not date_str:
            return None
        try:
            # Try full datetime format
            dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            try:
                # Try just date format
                dt = datetime.strptime(date_str, "%Y-%m-%d")
                if is_end_date:
                    # If it's an end date, set time to the very end of that day
                    dt = dt.replace(hour=23, minute=59, second=59)
            except ValueError:
                logger.error(f"Invalid date format: {date_str}. Use 'YYYY-MM-DD' or 'YYYY-MM-DD HH:MM:SS'.")
                return "ERROR"  # Use a sentinel to stop execution

        # Assume the provided time is in UTC and get the timestamp
        return str(dt.replace(tzinfo=timezone.utc).timestamp())

    oldest_ts = convert_date_to_timestamp(oldest_str, is_end_date=False)
    if oldest_ts == "ERROR":
        return False # Indicate failure
    if oldest_ts:
        logger.info(f"Setting 'oldest' timestamp (start date) to: {oldest_ts} (from {oldest_str} UTC) for channel {channel_id}")

    latest_ts = convert_date_to_timestamp(latest_str, is_end_date=True)
    if latest_ts == "ERROR":
        return False # Indicate failure
    if latest_ts:
        logger.info(f"Setting 'latest' timestamp (end date) to: {latest_ts} (from {latest_str} UTC) for channel {channel_id}")

    all_messages = []
    next_cursor = None
    page_count = 0
    retry_count = 0
    max_retries = 3 # Max retries for rate limiting

    logger.info(f"Starting message export for channel: {channel_id}")

    try:
        while True:
            page_count += 1
            logger.info(f"Fetching page {page_count} for channel {channel_id}...")
            try:
                response = client.conversations_history(
                    channel=channel_id,
                    limit=200,  # Max recommended limit per page
                    cursor=next_cursor,
                    oldest=oldest_ts,  # Pass 'oldest' timestamp to the API
                    latest=latest_ts   # Pass 'latest' timestamp to the API
                )
                retry_count = 0 # Reset retry count on success

            except SlackApiError as e:
                logger.error(f"Slack API Error for channel {channel_id} (Page {page_count}): {e.response['error']}")
                logger.debug(f"Response details: {e.response}")
                
                if e.response['error'] == 'ratelimited' and retry_count < max_retries:
                    retry_count += 1
                    retry_after = int(e.response.headers.get('Retry-After', REQUEST_DELAY_SECONDS * (2**retry_count))) # Exponential backoff
                    logger.warning(f"Rate limited. Retrying after {retry_after} seconds... (Attempt {retry_count}/{max_retries})")
                    time.sleep(retry_after)
                    page_count -= 1 # Decrement page count to retry the same page
                    continue # Retry the loop
                elif e.response['error'] == 'channel_not_found':
                    logger.error(f"Channel {channel_id} not found or bot does not have access. Skipping.")
                    return False # Indicate failure for this channel
                elif e.response['error'] == 'not_in_channel':
                     logger.error(f"Bot is not in private channel {channel_id}. Please invite the bot. Skipping.")
                     return False # Indicate failure for this channel
                else:
                     logger.error(f"Stopping export for channel {channel_id} due to unhandled API error: {e.response['error']}")
                     return False # Indicate failure for this channel

            except Exception as e:
                 logger.error(f"An unexpected error occurred during API call for channel {channel_id}: {e}", exc_info=True)
                 return False # Indicate failure for this channel


            if not response.get("ok"):
                 # This case might be redundant now with the try/except SlackApiError, but kept for safety
                 logger.error(f"API Error (non-exception): {response.get('error', 'Unknown error')} for channel {channel_id}")
                 break

            messages = response.get("messages", [])
            all_messages.extend(messages)
            logger.info(f"Fetched {len(messages)} messages on page {page_count} for channel {channel_id}. Total fetched: {len(all_messages)}")

            # --- Pagination ---
            response_metadata = response.get("response_metadata")
            if response_metadata:
                next_cursor = response_metadata.get("next_cursor")
            else:
                next_cursor = None # Should not happen if there are messages, but good to handle

            if not next_cursor:
                logger.info(f"Reached the end of the message history for channel {channel_id}.")
                break

            # --- Rate Limiting ---
            # Wait before the next request to avoid hitting rate limits
            logger.debug(f"Waiting {REQUEST_DELAY_SECONDS} seconds before next request...")
            time.sleep(REQUEST_DELAY_SECONDS)

    except Exception as e: # Catch broader exceptions during the loop logic itself
        logger.error(f"An unexpected error occurred during pagination loop for channel {channel_id}: {e}", exc_info=True)
        return False # Indicate failure

    # --- Save to File ---
    if all_messages:
        logger.info(f"Saving {len(all_messages)} messages to {output_file}")
        try:
            # Sort messages by timestamp (oldest first) - Slack usually returns newest first
            all_messages.sort(key=lambda x: float(x.get('ts', 0)))

            with open(output_file, 'w', encoding='utf-8') as f:
                json.dump(all_messages, f, ensure_ascii=False, indent=4)
            logger.info(f"Successfully saved message history for channel {channel_id}.")
            return True # Indicate success
        except IOError as e:
            logger.error(f"Failed to write messages to file {output_file}: {e}")
            return False # Indicate failure
        except Exception as e:
            logger.error(f"An unexpected error occurred during file writing for {output_file}: {e}", exc_info=True)
            return False # Indicate failure
    else:
        logger.warning(f"No messages were fetched for channel {channel_id} (or an error occurred before saving).")
        # Consider if an empty file should be created or not. Currently, it's not.
        return True # Indicate success (no messages is not a failure)


if __name__ == "__main__":
    # --- IMPORTANT SAFETY & COMPLIANCE WARNING ---
    # (Warning text remains the same)
    print("\n" + "="*60)
    print("WARNING:")
    print("Before running this script, ensure you have explicit authorization")
    print("from your Slack workspace administrators and that your actions")
    print("comply with:")
    print("  1. Your organization's data access and privacy policies.")
    print("  2. Slack's Terms of Service (https://slack.com/terms-of-service).")
    print("Unauthorized exporting of data can have serious consequences.")
    print("="*60 + "\n")
    # You might want to add an explicit confirmation step here in a real application
    # input("Press Enter to continue if you have the necessary permissions...")

    channels_to_export = load_channels_from_json(CHANNELS_FILE)

    if not channels_to_export:
        logger.error("Could not load channels. Exiting.")
        sys.exit(1) # Exit with an error code

    # Create the main output directory if it doesn't exist
    if not os.path.exists(OUTPUT_DIR):
        try:
            os.makedirs(OUTPUT_DIR)
            logger.info(f"Created main output directory: {OUTPUT_DIR}")
        except OSError as e:
            logger.error(f"Failed to create main output directory {OUTPUT_DIR}: {e}. Exiting.")
            sys.exit(1)


    total_channels = len(channels_to_export)
    success_count = 0
    failure_count = 0

    logger.info(f"Starting export process for {total_channels} channels...")

    for i, channel_info in enumerate(channels_to_export):
        channel_id = channel_info.get("id")
        channel_name = channel_info.get("displayName", channel_id) # Use display name if available, else ID
        
        logger.info(f"\n--- Processing channel {i+1}/{total_channels}: {channel_name} ({channel_id}) ---")

        # Construct output file path within the directory
        output_filename = f"{channel_id}_history.json"
        output_filepath = os.path.join(OUTPUT_DIR, output_filename)

        success = fetch_channel_history(
            SLACK_BOT_TOKEN,
            channel_id,
            output_filepath, # Use the constructed path
            oldest_str=START_DATE_STR,
            latest_str=END_DATE_STR
        )

        if success:
            success_count += 1
        else:
            failure_count += 1

        # Optional: Add a small delay between processing different channels
        if i < total_channels - 1:
             time.sleep(1) # Small pause before the next channel

    logger.info("\n--- Export Summary ---")
    logger.info(f"Total channels processed: {total_channels}")
    logger.info(f"Successfully exported: {success_count}")
    logger.info(f"Failed/Skipped: {failure_count}")
    logger.info("Export process finished.")

