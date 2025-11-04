import argparse
import os
import sys
from src.utils import (
    setup_logging,
    load_json_file,
    save_json_file,
    convert_date_to_timestamp,
    create_directory,
    format_timestamp,
    sanitize_filename,
    validate_channels_json,
    validate_channel_id,
)
from src.slack_client import SlackClient
from src.google_drive import GoogleDriveClient
from slack_sdk.errors import SlackApiError

logger = setup_logging()

def preprocess_history(history_data, slack_client, people_cache=None):
    """Processes Slack history into a human-readable format."""
    threads = {}
    for message in history_data:
        text = message.get('text', '')
        files = message.get('files')

        # If no text and no files, skip
        if not text and not files:
            continue
        
        # If no text but has files, use a placeholder
        if not text and files:
            text = "[File attached]"
        # If text and files, append placeholder
        elif text and files:
            text += " [File attached]"

        thread_key = message.get('thread_ts', message.get('ts'))
        if not thread_key:
            continue

        if thread_key not in threads:
            threads[thread_key] = []
        
        ts = message.get('ts')
        
        user_id = message.get('user')
        name = "Unknown User"
        if user_id:
            user_info = slack_client.get_user_info(user_id)
            if user_info:
                name = user_info.get("displayName", message.get('username', user_id))
        
        text = text.replace('\n', '\n    ')
        
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
    
    channel_id = channel_info.get("id")
    
    # For group DMs, create a name from participants
    if channel_info.get("is_mpim"):
        members = channel_info.get("members", [])
        names = []
        for member_id in members:
            user_info = slack_client.get_user_info(member_id)
            if user_info:
                names.append(user_info.get("displayName", member_id))
        if names:
            return ", ".join(sorted(names))
    
    # For DMs, get the other user's name
    if channel_info.get("is_im"):
        other_user_id = channel_info.get("user")
        if other_user_id:
            user_info = slack_client.get_user_info(other_user_id)
            if user_info:
                return user_info.get("displayName", other_user_id)
    
    # For channels, use name or fallback to ID
    return channel_info.get("name") or channel_id

def main(args):
    """Main function to run the Slack history export and upload process."""
    # Get configuration from environment variables with validation
    slack_bot_token = os.getenv("SLACK_BOT_TOKEN", "").strip()
    google_drive_credentials_file = os.getenv("GOOGLE_DRIVE_CREDENTIALS_FILE", "").strip()
    google_drive_folder_id = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "").strip()
    
    if not slack_bot_token:
        logger.error("SLACK_BOT_TOKEN environment variable is required and cannot be empty. Exiting.")
        sys.exit(1)
    
    if not google_drive_credentials_file:
        logger.error("GOOGLE_DRIVE_CREDENTIALS_FILE environment variable is required and cannot be empty. Exiting.")
        sys.exit(1)
    
    # Validate credentials file exists and is a file
    if not os.path.exists(google_drive_credentials_file):
        logger.error(f"Credentials file not found: {google_drive_credentials_file}")
        sys.exit(1)
    if not os.path.isfile(google_drive_credentials_file):
        logger.error(f"Credentials path is not a file: {google_drive_credentials_file}")
        sys.exit(1)
    
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

        # Validate JSON structure
        try:
            validate_channels_json(channels_data)
        except ValueError as e:
            logger.error(f"Invalid channels.json structure: {e}")
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
            
            # Validate channel ID format
            if not channel_id or not validate_channel_id(channel_id):
                logger.warning(f"Invalid channel ID format: {channel_id}. Skipping.")
                continue
            
            channel_name = get_conversation_display_name(channel_info, slack_client)
            
            logger.info(f"--- Processing conversation: {channel_name} ({channel_id}) ---")

            # Validate timestamps if provided
            oldest_ts = convert_date_to_timestamp(args.start_date)
            latest_ts = convert_date_to_timestamp(args.end_date, is_end_date=True)
            if args.start_date and oldest_ts is None:
                logger.error(f"Invalid start date format: {args.start_date}")
                continue
            if args.end_date and latest_ts is None:
                logger.error(f"Invalid end date format: {args.end_date}")
                continue

            history = slack_client.fetch_channel_history(
                channel_id,
                oldest_ts=oldest_ts,
                latest_ts=latest_ts
            )

            if history:
                processed_history = preprocess_history(history, slack_client, people_cache)
                
                # Sanitize filename to prevent path traversal
                safe_channel_name = sanitize_filename(channel_name)
                output_filename = f"{safe_channel_name}_history.txt"
                output_filepath = os.path.join(output_dir, output_filename)
                
                # Additional safety check - ensure path is within output_dir
                abs_output_dir = os.path.abspath(output_dir)
                abs_output_filepath = os.path.abspath(output_filepath)
                if not abs_output_filepath.startswith(abs_output_dir):
                    logger.error(f"Invalid file path detected: {output_filepath}. Skipping.")
                    continue
                
                try:
                    with open(output_filepath, 'w', encoding='utf-8') as f:
                        f.write(processed_history)
                        f.flush()
                        os.fsync(f.fileno())  # Ensure data is written to disk
                    
                    # Verify file was written successfully
                    if not os.path.exists(output_filepath) or os.path.getsize(output_filepath) == 0:
                        logger.error(f"File write verification failed for {output_filepath}")
                        continue
                    
                    logger.info(f"Saved processed history to {output_filepath}")
                except IOError as e:
                    logger.error(f"Failed to write file {output_filepath}: {e}")
                    continue
                except Exception as e:
                    logger.error(f"Unexpected error writing file {output_filepath}: {e}", exc_info=True)
                    continue

                if args.upload_to_drive:
                    folder_id = google_drive_client.create_folder(channel_name, google_drive_folder_id)
                    if folder_id:
                        file_id = google_drive_client.upload_file(output_filepath, folder_id)
                        if not file_id:
                            logger.error(f"Failed to upload file for {channel_name}. Skipping sharing.")
                            continue
                        
                        # Share with members (with rate limiting)
                        members = slack_client.get_channel_members(channel_id)
                        shared_emails = set()
                        share_errors = []
                        import time
                        for i, member_id in enumerate(members):
                            # Rate limit: pause every N shares to avoid API limits
                            if i > 0 and i % SlackClient.SHARE_RATE_LIMIT_INTERVAL == 0:
                                time.sleep(SlackClient.SHARE_RATE_LIMIT_DELAY)
                            
                            user_info = slack_client.get_user_info(member_id)
                            if user_info and user_info.get("email"):
                                email = user_info["email"]
                                if email not in shared_emails:
                                    try:
                                        shared = google_drive_client.share_folder(folder_id, email)
                                        if shared:
                                            shared_emails.add(email)
                                        else:
                                            share_errors.append(f"{email}: share failed")
                                    except Exception as e:
                                        share_errors.append(f"{email}: {str(e)}")
                        
                        if share_errors:
                            logger.warning(f"Failed to share with some users: {', '.join(share_errors)}")
                        
                        logger.info(f"Shared folder '{channel_name}' with {len(shared_emails)} participants")
            else:
                logger.warning(f"No history found for {channel_name} ({channel_id})")


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
