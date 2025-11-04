import argparse
import os
from src.utils import (
    setup_logging,
    load_json_file,
    save_json_file,
    convert_date_to_timestamp,
    create_directory,
    format_timestamp,
)
from src.slack_client import SlackClient
from src.google_drive import GoogleDriveClient

logger = setup_logging()

def get_display_name(message, slack_client, people_cache=None):
    """Gets the friendly display name for a message, looking up from Slack API if needed."""
    user_id = message.get('user')
    if not user_id:
        return "Unknown User"
    
    # First check cache (from people.json pre-warming)
    if people_cache and user_id in people_cache:
        return people_cache[user_id]
    
    # Look up from Slack API (cached in slack_client)
    user_info = slack_client.get_user_info(user_id)
    if user_info:
        display_name = user_info.get("displayName")
        if display_name:
            # Update cache for future use
            if people_cache is not None:
                people_cache[user_id] = display_name
            return display_name
    
    # Fallback to username if available
    if 'username' in message:
        return message['username']
    
    # Last resort: return user ID
    return user_id

def preprocess_history(history_data, slack_client, people_cache=None):
    """Processes Slack history into a human-readable format."""
    threads = {}
    for message in history_data:
        if message.get('text') is None:
            continue
        
        thread_key = message.get('thread_ts', message.get('ts'))
        if not thread_key:
            continue

        if thread_key not in threads:
            threads[thread_key] = []
        
        ts = message.get('ts')
        name = get_display_name(message, slack_client, people_cache)
        text = message.get('text', '').replace('\n', '\n    ')
        
        threads[thread_key].append((ts, name, text))

    sorted_thread_keys = sorted(threads.keys())
    output_lines = []
    for thread_key in sorted_thread_keys:
        messages_in_thread = sorted(threads[thread_key], key=lambda m: m[0])
        
        parent_ts, parent_name, parent_text = messages_in_thread[0]
        formatted_time = format_timestamp(parent_ts)
        output_lines.append(f"[{formatted_time}] {parent_name}: {parent_text}")
        
        for (reply_ts, reply_name, reply_text) in messages_in_thread[1:]:
            formatted_reply_time = format_timestamp(reply_ts)
            output_lines.append(f"    > [{formatted_reply_time}] {reply_name}: {reply_text}")
        
        output_lines.append("\n")
    
    return "\n".join(output_lines)

def get_conversation_display_name(channel_info, slack_client):
    """Gets the display name for a conversation, handling channels, DMs, and group chats."""
    display_name = channel_info.get("displayName")
    if display_name:
        return display_name
    
    # Try to get name from Slack API
    channel_id = channel_info.get("id")
    try:
        response = slack_client.client.conversations_info(channel=channel_id)
        channel = response.get("channel", {})
        
        # For group DMs, create a name from participants
        if channel.get("is_mpim"):
            members = channel.get("members", [])
            names = []
            for member_id in members:
                user_info = slack_client.get_user_info(member_id)
                if user_info:
                    names.append(user_info.get("displayName", member_id))
            if names:
                return ", ".join(sorted(names))
        
        # For DMs, get the other user's name
        if channel.get("is_im"):
            other_user_id = channel.get("user")
            if other_user_id:
                user_info = slack_client.get_user_info(other_user_id)
                if user_info:
                    return user_info.get("displayName", other_user_id)
        
        # For channels, use name or fallback to ID
        return channel.get("name") or channel_id
    except Exception as e:
        logger.warning(f"Could not fetch conversation info for {channel_id}: {e}")
        return channel_id

def main(args):
    """Main function to run the Slack history export and upload process."""
    # Get configuration from environment variables
    slack_bot_token = os.getenv("SLACK_BOT_TOKEN")
    google_drive_credentials_file = os.getenv("GOOGLE_DRIVE_CREDENTIALS_FILE")
    google_drive_folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID")
    
    if not slack_bot_token:
        logger.error("SLACK_BOT_TOKEN environment variable is required. Exiting.")
        return
    
    if not google_drive_credentials_file:
        logger.error("GOOGLE_DRIVE_CREDENTIALS_FILE environment variable is required. Exiting.")
        return
    
    if not google_drive_folder_id:
        logger.warning("GOOGLE_DRIVE_FOLDER_ID not set. Files will be uploaded to Drive root.")
    
    slack_client = SlackClient(slack_bot_token)
    google_drive_client = GoogleDriveClient(google_drive_credentials_file)

    if args.make_ref_files:
        logger.info("Fetching all conversations and users to create reference files...")
        channels = slack_client.get_all_channels()
        
        # Add export flag (defaults to true) to each conversation
        # Preserve existing export flags if channels.json already exists
        existing_channels_data = load_json_file("config/channels.json")
        existing_export_map = {}
        if existing_channels_data:
            for ch in existing_channels_data.get("channels", []):
                if "id" in ch:
                    existing_export_map[ch["id"]] = ch.get("export", True)
        
        channels_with_export = []
        for channel in channels:
            channel_entry = dict(channel)
            # Preserve existing export setting, or default to True
            if channel_entry.get("id") in existing_export_map:
                channel_entry["export"] = existing_export_map[channel_entry.get("id")]
            elif "export" not in channel_entry:
                channel_entry["export"] = True
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
        logger.info(f"Found {len(channels_with_export)} conversations. Set 'export: false' in channels.json to exclude any you don't want to export.")

    if args.export_history:
        channels_data = load_json_file("config/channels.json")
        if not channels_data:
            logger.error("Could not load channels from config/channels.json. Exiting.")
            logger.info("Run with --make-ref-files first to generate channels.json")
            return

        # Filter to only conversations marked for export (export defaults to True if not specified)
        channels_to_export = [
            ch for ch in channels_data.get("channels", [])
            if ch.get("export", True) is True
        ]
        
        if not channels_to_export:
            logger.warning("No conversations marked for export. Set 'export: true' in channels.json for conversations you want to export.")
            return
        
        logger.info(f"Found {len(channels_to_export)} conversation(s) to export")

        # Load people.json as a cache/pre-warming mechanism (optional - will lookup on-demand if missing)
        people_cache = {}
        people_json = load_json_file("config/people.json")
        if people_json:
            people_cache = {p["slackId"]: p["displayName"] for p in people_json.get("people", [])}
            logger.info(f"Loaded {len(people_cache)} users from people.json cache")
        else:
            logger.info("No people.json found - will lookup users on-demand from Slack API")
        
        output_dir = "slack_exports"
        create_directory(output_dir)

        for channel_info in channels_to_export:
            channel_id = channel_info.get("id")
            channel_name = get_conversation_display_name(channel_info, slack_client)
            
            logger.info(f"--- Processing conversation: {channel_name} ({channel_id}) ---")

            history = slack_client.fetch_channel_history(
                channel_id,
                oldest_ts=convert_date_to_timestamp(args.start_date),
                latest_ts=convert_date_to_timestamp(args.end_date, is_end_date=True)
            )

            if history:
                processed_history = preprocess_history(history, slack_client, people_cache)
                output_filename = f"{channel_name}_history.txt"
                output_filepath = os.path.join(output_dir, output_filename)
                
                with open(output_filepath, 'w', encoding='utf-8') as f:
                    f.write(processed_history)
                
                logger.info(f"Saved processed history to {output_filepath}")

                if args.upload_to_drive:
                    folder_id = google_drive_client.create_folder(channel_name, google_drive_folder_id)
                    if folder_id:
                        google_drive_client.upload_file(output_filepath, folder_id)
                        
                        # Share with members
                        members = slack_client.get_channel_members(channel_id)
                        shared_emails = set()
                        for member_id in members:
                            user_info = slack_client.get_user_info(member_id)
                            if user_info and user_info.get("email"):
                                email = user_info["email"]
                                if email not in shared_emails:
                                    google_drive_client.share_folder(folder_id, email)
                                    shared_emails.add(email)
                        
                        logger.info(f"Shared folder '{channel_name}' with {len(shared_emails)} participants")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export Slack conversations and upload to Google Drive.")
    parser.add_argument("--make-ref-files", action="store_true", help="Generate reference files (channels.json, people.json).")
    parser.add_argument("--export-history", action="store_true", help="Export conversation history.")
    parser.add_argument("--upload-to-drive", action="store_true", help="Upload exported files to Google Drive.")
    parser.add_argument("--start-date", help="Start date for history export (YYYY-MM-DD).")
    parser.add_argument("--end-date", help="End date for history export (YYYY-MM-DD).")
    
    args = parser.parse_args()

    if not any([args.make_ref_files, args.export_history, args.upload_to_drive]):
        parser.print_help()
    else:
        main(args)
