# Slack Feeder

This project exports conversations from Slack, processes them into a human-readable format, and uploads them to Google Drive. This allows for easy sharing and consumption of Slack conversations by other tools, such as Gemini.

## Features

- Export conversation history from public channels, private channels, DMs, and group chats
- Processes Slack's JSON export into a clean, readable text format with human-readable timestamps
- Creates and organizes conversations in Google Drive folders named with display names
- Automatically shares folders with all conversation participants
- Uses environment variables for secure configuration (perfect for CI/CD pipelines)
- Handles existing folders gracefully (reuses existing folders, updates files)

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/jflowers/slackfeeder.git
cd slackfeeder
```

### 2. Install dependencies

**Python Requirements:** Python 3.9 or higher is required.

```bash
pip install -r requirements.txt
```

### 3. Configure the application

This project uses environment variables for configuration, making it safe to host in public repositories while keeping secrets secure.

**Option 1: Using a .env file (Recommended for local development)**

Create a `.env` file in the project root directory:

```bash
cp .env.example .env
```

Then edit `.env` and fill in your actual values:

```bash
SLACK_BOT_TOKEN=xoxb-your-actual-token-here
GOOGLE_DRIVE_CREDENTIALS_FILE=./path/to/credentials.json
GOOGLE_DRIVE_FOLDER_ID=your-folder-id-optional
```

**Option 2: Using environment variables directly**

For CI/CD pipelines or when you prefer to set environment variables directly:

- **`SLACK_BOT_TOKEN`**: Your Slack bot token (starts with `xoxb-`)
  - Create a Slack app at https://api.slack.com/apps
  - Grant it the following OAuth scopes:
    - `channels:history`, `channels:read`
    - `groups:history`, `groups:read`
    - `im:history`, `im:read`
    - `mpim:history`, `mpim:read`
    - `users:read`, `users:read.email`
  - Install the app to your workspace and copy the bot token

- **`GOOGLE_DRIVE_CREDENTIALS_FILE`**: Path to your Google Drive API credentials JSON file
  - Create a Google Cloud project at https://console.cloud.google.com
  - Enable the Google Drive API
  - Create OAuth 2.0 credentials (Desktop app type)
  - Download the credentials as a JSON file
  - **Important:** Keep this file secure and never commit it to version control

- **`GOOGLE_DRIVE_FOLDER_ID`**: (Optional) ID of the Google Drive folder where conversations should be stored
  - If not set, files will be uploaded to Drive root
  - To find a folder ID, open it in Google Drive and copy the ID from the URL

```bash
export SLACK_BOT_TOKEN="xoxb-your-token-here"
export GOOGLE_DRIVE_CREDENTIALS_FILE="./path/to/credentials.json"
export GOOGLE_DRIVE_FOLDER_ID="your-folder-id"
```

**For GitLab CI/CD:**
Set these as CI/CD variables in your GitLab project settings under Settings ? CI/CD ? Variables.

**For other CI/CD systems:**
Set these as environment variables in your build/deployment configuration.

**Note:** If you use a `.env` file for local development, it will be automatically loaded. Environment variables set directly in your shell will override values in `.env`.

## Usage

### 1. Generate Reference Files

First, generate reference files for all conversations and users your bot has access to:

```bash
python src/main.py --make-ref-files
```

This will create:
- `config/channels.json` - List of all conversations (channels, DMs, group chats) with export flags
- `config/people.json` - List of all users with their display names and emails (used as a performance cache)

**Note:** If you're cloning this repository, you'll need to copy the example files first:

```bash
cp config/channels.json.example config/channels.json
cp config/people.json.example config/people.json
```

Then run `--make-ref-files` to populate them with your actual data.

### 2. Configure Conversations to Export

Edit `config/channels.json` to control which conversations to export. By default, all conversations have `"export": true`. Set `"export": false"` to exclude conversations you don't want:

```json
{
    "channels": [
        {
            "id": "C04KU2JTDJR",
            "displayName": "team-orange",
            "export": true,
            "share": true
        },
        {
            "id": "C05LGUSIA25",
            "displayName": "general",
            "export": false
        },
        {
            "id": "D1234567890",
            "export": true,
            "share": false
        }
    ]
}
```

- If `export` is not specified, it defaults to `true` (will be exported)
- If `share` is not specified, it defaults to `true` (folder will be shared with participants)
- Set `"share": false"` to export the conversation but not share the folder with participants
- If `displayName` is not provided, the script will automatically fetch it from Slack (for channels) or construct it from participant names (for DMs and group chats)

**Note:** `people.json` is optional but recommended - it speeds up processing by avoiding API lookups for known users. The system will automatically look up new users on-demand if they're not in the cache.

**Configuration Files:** The `config/channels.json` and `config/people.json` files are **not tracked in git** (they're in `.gitignore`) because they contain user-specific settings. Example files (`config/channels.json.example` and `config/people.json.example`) are provided as templates. If you're forking this repository, you can commit your config files to your fork - they won't conflict with upstream updates since they're ignored in the upstream repository.

### 3. Export and Upload

To export conversations and upload them to Google Drive:

```bash
python src/main.py --export-history --upload-to-drive
```

You can also specify a date range for the export:

```bash
python src/main.py --export-history --upload-to-drive --start-date "2024-01-01" --end-date "2024-12-31"
```

### Example: Running Multiple Times

**First Run:**
```bash
python src/main.py --export-history --upload-to-drive
# Output: Fetches all messages, creates folders, uploads files, shares with participants
# Participants receive email notifications
```

**Subsequent Runs (Weekly):**
```bash
python src/main.py --export-history --upload-to-drive
# Output: Only fetches new messages since last export
# Creates new dated files in existing folders
# No duplicate notifications sent (permissions already exist)
```

**Example Output:**
```
2025-11-05 10:29:22 - INFO - Found 2 conversation(s) to export
2025-11-05 10:29:22 - INFO - --- Processing conversation: Carol Burnett, Betty White (C09WIPCPA2F) ---
2025-11-05 10:29:22 - INFO - Fetching messages since last export: 2025-11-05 14:24:41 UTC
2025-11-05 10:29:23 - INFO - Fetched 9 messages on page 1
2025-11-05 10:29:25 - INFO - Uploaded file 'Carol Burnett, Betty White_history_2025-11-05_15-29-23.txt'
2025-11-05 10:29:28 - INFO - Saved export metadata for Carol Burnett, Betty White
2025-11-05 10:29:34 - INFO - Shared folder 'Carol Burnett, Betty White' with 3 participants
```

## How it Works

1. **`--make-ref-files`**:
   - Connects to the Slack API using your bot token
   - Fetches all conversations the bot is a member of (channels, DMs, group chats)
   - Fetches user information for all participants
   - Saves this information to `config/channels.json` and `config/people.json`

2. **`--export-history`**:
   - Reads the list of conversations from `config/channels.json`
   - Filters to conversations with `export: true` (or missing, which defaults to true)
   - For each conversation, fetches the message history from the Slack API
   - Processes the history, replacing user IDs with display names:
     - Uses `people.json` as a performance cache (optional - speeds up processing)
     - Looks up users on-demand from Slack API if not in cache
     - Automatically handles new users who joined after `people.json` was created
     - Caches API results to minimize API calls
   - Formats messages with human-readable timestamps and thread structure
   - Saves processed files to the `slack_exports` directory

3. **`--upload-to-drive`**:
   - Connects to the Google Drive API
   - For each exported conversation, creates a folder (or uses existing) named with the conversation's display name
   - Uploads the processed text file to the folder (each file has a unique timestamp to prevent overwriting)
   - Automatically tracks the last export date for incremental updates:
     - Creates/updates a metadata file (`{channel_name}_last_export.json`) in each folder
     - Stores the timestamp of the latest message exported
     - Used by subsequent runs to determine what's new
   - Checks existing folder permissions before sharing:
     - If user already has access, skips the share API call (prevents duplicate notifications)
     - Only shares with users who don't already have access
     - Users receive email notifications only on first share
   - Shares the folder with all conversation participants via email
   - On subsequent runs, only fetches messages since the last export

## Folder Structure

Each conversation gets its own folder in Google Drive, named with the conversation's display name:
- Channels: Uses the channel name (e.g., `team-orange`)
- DMs: Uses the other participant's name (e.g., `John Doe`)
- Group chats: Uses comma-separated participant names (e.g., `John Doe, Jane Smith`)

All participants are automatically added as viewers to the folder, allowing them to access the conversation history.

**Metadata Files:** Each folder contains a small metadata file (`{channel_name}_last_export.json`) that tracks the last export timestamp. This file:
- Is created automatically on first export
- Is updated after each successful export
- Contains the timestamp of the latest message exported
- Is used by subsequent runs to determine incremental updates
- Is visible in the folder but is small and unobtrusive
- Example content:
  ```json
  {
    "latest_message_timestamp": 1730826281.234,
    "updated_at": "2025-11-05T14:24:41.234567+00:00"
  }
  ```

## Weekly/Incremental Exports

The script is designed to run weekly and automatically performs incremental updates. **It is fully stateless and works perfectly in CI/CD pipelines** - no local state files are required.

- **First run**: Fetches all available message history
- **Subsequent runs**: Automatically fetches only new messages since the last export
- **File naming**: Each export creates a new file with a timestamp (e.g., `channel_name_history_2024-01-15_14-30-45.txt`) to prevent overwriting
- **State management**: The script stores the last export timestamp in a small metadata file (`{channel_name}_last_export.json`) **in Google Drive**, not locally
- **CI/CD friendly**: Since state is stored in Drive, the script works perfectly in ephemeral CI/CD environments
- **Manual date ranges**: You can still use `--start-date` and `--end-date` to override the automatic incremental behavior

When you run the script weekly:
1. It checks Google Drive for the last export timestamp (from a metadata file in each folder)
2. Only fetches messages newer than that timestamp (unless `--start-date` is explicitly provided)
3. Creates a new dated file in the same folder
4. Saves the latest message timestamp to a metadata file in Drive (for next run)
5. Checks folder permissions before sharing:
   - If participants already have access, no additional notifications are sent
   - Only new participants receive email notifications
   - This prevents duplicate notifications on subsequent runs

**What Participants See:**
- **First share:** Participants receive an email notification from Google Drive that they've been given access to a folder
- **Subsequent runs:** No notifications sent (permissions already exist)
- **Folder contents:** All weekly export files accumulate in the folder, creating a complete history over time

This means you can share the folder with participants, and they'll see all the weekly export files accumulating over time. The script works identically whether run locally or in CI/CD - no state persistence between runs is required.

## Running in CI/CD

This project is designed to run in CI/CD pipelines. Example GitLab CI configuration:

```yaml
slack-export:
  image: python:3.11
  script:
    - pip install -r requirements.txt
    - python src/main.py --export-history --upload-to-drive
  only:
    - schedules  # Run on schedule
```

Set the required environment variables in GitLab CI/CD settings.

## Environment Variables Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `SLACK_BOT_TOKEN` | Yes | Slack bot token (starts with `xoxb-` or `xoxp-`) |
| `GOOGLE_DRIVE_CREDENTIALS_FILE` | Yes | Path to Google Drive API credentials JSON file |
| `GOOGLE_DRIVE_FOLDER_ID` | No | Google Drive folder ID where exports should be stored (defaults to Drive root) |
| `GOOGLE_DRIVE_TOKEN_FILE` | No | Path to store OAuth token (defaults to `~/.config/slackfeeder/token.json`) |
| `SLACK_EXPORT_OUTPUT_DIR` | No | Local directory for exported files (defaults to `slack_exports`) |
| `MAX_EXPORT_FILE_SIZE_MB` | No | Maximum file size in MB (defaults to 100) |
| `MAX_MESSAGES_PER_CONVERSATION` | No | Maximum messages per conversation (defaults to 50000) |
| `MAX_DATE_RANGE_DAYS` | No | Maximum date range in days (defaults to 365) |
| `LOG_LEVEL` | No | Logging level: DEBUG, INFO, WARNING, ERROR (defaults to INFO) |

## Troubleshooting

### Common Issues

**Issue: "Could not load channels from config/channels.json"**
- **Solution:** Copy the example file: `cp config/channels.json.example config/channels.json`
- Then run `python src/main.py --make-ref-files` to populate it

**Issue: "Failed to obtain valid credentials"**
- **Solution:** Check that your `GOOGLE_DRIVE_CREDENTIALS_FILE` path is correct and the file exists
- If using a `.env` file, ensure the path is relative to the project root or absolute

**Issue: "Token file permissions are insecure"**
- **Solution:** The token file must have permissions 600 (owner read/write only)
- Fix: `chmod 600 ~/.config/slackfeeder/token.json`

**Issue: "Rate limited" errors**
- **Solution:** The script includes automatic rate limiting and retries. If you see frequent rate limit errors:
  - The script will automatically retry with exponential backoff
  - Reduce the number of conversations exported at once
  - Add delays between runs if running multiple times

**Issue: "No previous export found in Drive, fetching all messages"**
- **Solution:** This is expected on first run. Subsequent runs will be incremental.

**Issue: Participants receiving duplicate notifications**
- **Solution:** This shouldn't happen - the script checks permissions before sharing. If it does:
  - Check that you're using the latest version
  - Verify the permissions are being checked correctly (check logs for "already shared" messages)

**Issue: Files not appearing in Google Drive**
- **Solution:** 
  - Check that `GOOGLE_DRIVE_FOLDER_ID` is correct (if specified)
  - Verify the Google Drive API credentials have proper permissions
  - Check the logs for upload errors

## Limitations

- **Slack API Rate Limits:** The script respects Slack API rate limits with automatic retries, but very large workspaces may take time to process
- **Google Drive API Quotas:** Google Drive has quotas (default: 1,000 requests per 100 seconds per user). The script includes rate limiting to stay within quotas
- **Large Conversations:** Very large conversations (>50,000 messages) are limited by `MAX_MESSAGES_PER_CONVERSATION`
- **Date Range:** Maximum date range is limited to 365 days by default (configurable via `MAX_DATE_RANGE_DAYS`)
- **File Size:** Individual export files are limited to 100MB by default (configurable via `MAX_EXPORT_FILE_SIZE_MB`)
- **Bot Permissions:** The bot must be a member of channels/groups to export them. Private channels require the bot to be invited.

## Performance Notes

- **Rate Limiting:** The script includes built-in rate limiting for both Slack and Google Drive APIs
- **Caching:** User information is cached using `people.json` to minimize API calls
- **Incremental Exports:** Only fetches new messages, significantly reducing API calls on subsequent runs
- **Batch Processing:** Processes conversations sequentially with small delays to avoid rate limits
- **API Efficiency:** Permission checks prevent unnecessary share API calls

## FAQ

**Q: Will participants get notified every time I run the script?**
A: No. Participants receive email notifications only on the first share. Subsequent runs check permissions and skip sharing if the user already has access.

**Q: Can I run this without uploading to Google Drive?**
A: Yes. Use `--export-history` without `--upload-to-drive`. Files will be saved locally only. Note: Incremental exports only work with `--upload-to-drive` since state is stored in Drive.

**Q: What happens if I delete the metadata file?**
A: The script will treat it as a new export and fetch all messages. The metadata file will be recreated on the next successful export.

**Q: Can I export specific channels only?**
A: Yes. Set `"export": false` in `config/channels.json` for channels you don't want to export.

**Q: How do I reset incremental exports and start fresh?**
A: Either delete the metadata file (`{channel_name}_last_export.json`) in Google Drive, or use `--start-date` to override the automatic behavior.

**Q: Can I run this in CI/CD?**
A: Yes! The script is fully stateless and works perfectly in CI/CD pipelines. No local state files are required.

**Q: What permissions does the Slack bot need?**
A: The bot needs these OAuth scopes:
- `channels:history`, `channels:read`
- `groups:history`, `groups:read`
- `im:history`, `im:read`
- `mpim:history`, `mpim:read`
- `users:read`, `users:read.email`

**Q: Can I export a conversation without sharing it with participants?**
A: Yes. Set `"share": false"` in `config/channels.json` for that conversation. The folder will be created and files uploaded, but participants won't be given access.

**Q: Can I export archived channels?**
A: The script skips archived channels by default. To export them, you would need to modify the code.

## Contributing

Contributions are welcome! Please open an issue or submit a pull request.
