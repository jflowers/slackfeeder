import argparse
import os
from src.utils import (
    setup_logging,
    load_json_file,
    save_json_file,
    convert_date_to_timestamp,
    create_directory,
)
from src.slack_client import SlackClient
from src.google_drive import GoogleDriveClient

logger = setup_logging()

def get_display_name(message, people_map):
    """Gets the friendly display name for a message."""
    user_id = message.get('user')
    if user_id in people_map:
        return people_map[user_id]
    if 'username' in message:
        return message['username']
    if user_id:
        return user_id
    return "Unknown User"

def preprocess_history(history_data, people_map):
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
        name = get_display_name(message, {"slackId": "test", "displayName": "test"})
        text = message.get('text', '').replace('\n', '\n    ')
        
        threads[thread_key].append((ts, name, text))

    sorted_thread_keys = sorted(threads.keys())
    output_lines = []
    for thread_key in sorted_thread_keys:
        messages_in_thread = sorted(threads[thread_key], key=lambda m: m[0])
        
        parent_ts, parent_name, parent_text = messages_in_thread[0]
        output_lines.append(f"[{parent_ts}] {parent_name}: {parent_text}")
        
        for (reply_ts, reply_name, reply_text) in messages_in_thread[1:]:
            output_lines.append(f"    > [{reply_ts}] {reply_name}: {reply_text}")
        
        output_lines.append("\n")
    
    return "\n".join(output_lines)

def main(args):
    """Main function to run the Slack history export and upload process."""
    config = load_json_file(args.config)
    if not config:
        logger.error("Configuration file not found or invalid. Exiting.")
        return

    slack_client = SlackClient(config.get("slack_bot_token"))
    google_drive_client = GoogleDriveClient(config.get("google_drive_credentials_file"))

    if args.make_ref_files:
        logger.info("Fetching all channels and users to create reference files...")
        channels = slack_client.get_all_channels()
        people = {}
        for channel in channels:
            members = slack_client.get_channel_members(channel["id"])
            for member_id in members:
                if member_id not in people:
                    user_info = slack_client.get_user_info(member_id)
                    if user_info:
                        people[member_id] = user_info
        
        save_json_file({"channels": channels}, "config/channels.json")
        save_json_file({"people": list(people.values())}, "config/people.json")
        logger.info("Reference files created successfully.")

    if args.export_history:
        channels_to_export = load_json_file("config/conversations.json")
        if not channels_to_export:
            logger.error("Could not load channels from config/conversations.json. Exiting.")
            return

        people_map = {p["slackId"]: p["displayName"] for p in load_json_file("config/people.json").get("people", [])}
        
        output_dir = "slack_exports"
        create_directory(output_dir)

        for channel_info in channels_to_export.get("channels", []):
            channel_id = channel_info.get("id")
            channel_name = channel_info.get("displayName", channel_id)
            
            logger.info(f"--- Processing channel: {channel_name} ({channel_id}) ---")

            history = slack_client.fetch_channel_history(
                channel_id,
                oldest_ts=convert_date_to_timestamp(args.start_date),
                latest_ts=convert_date_to_timestamp(args.end_date, is_end_date=True)
            )

            if history:
                processed_history = preprocess_history(history, people_map)
                output_filename = f"{channel_name}_history.txt"
                output_filepath = os.path.join(output_dir, output_filename)
                
                with open(output_filepath, 'w', encoding='utf-8') as f:
                    f.write(processed_history)
                
                logger.info(f"Saved processed history to {output_filepath}")

                if args.upload_to_drive:
                    folder_id = google_drive_client.create_folder(channel_name, config.get("google_drive_folder_id"))
                    if folder_id:
                        google_drive_client.upload_file(output_filepath, folder_id)
                        
                        # Share with members
                        members = slack_client.get_channel_members(channel_id)
                        for member_id in members:
                            user_info = slack_client.get_user_info(member_id)
                            if user_info and user_info.get("email"):
                                google_drive_client.share_folder(folder_id, user_info["email"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Export Slack conversations and upload to Google Drive.")
    parser.add_argument("--config", default="config/config.json", help="Path to the configuration file.")
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
