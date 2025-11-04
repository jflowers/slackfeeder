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

### 2. Configure Conversations to Export

Edit `config/channels.json` to control which conversations to export. By default, all conversations have `"export": true`. Set `"export": false"` to exclude conversations you don't want:

```json
{
    "channels": [
        {
            "id": "C04KU2JTDJR",
            "displayName": "team-orange",
            "export": true
        },
        {
            "id": "C05LGUSIA25",
            "displayName": "general",
            "export": false
        },
        {
            "id": "D1234567890"
        }
    ]
}
```

- If `export` is not specified, it defaults to `true` (will be exported)
- If `displayName` is not provided, the script will automatically fetch it from Slack (for channels) or construct it from participant names (for DMs and group chats)

**Note:** `people.json` is optional but recommended - it speeds up processing by avoiding API lookups for known users. The system will automatically look up new users on-demand if they're not in the cache.

### 3. Export and Upload

To export conversations and upload them to Google Drive:

```bash
python src/main.py --export-history --upload-to-drive
```

You can also specify a date range for the export:

```bash
python src/main.py --export-history --upload-to-drive --start-date "2024-01-01" --end-date "2024-12-31"
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
   - Automatically tracks the last export date for incremental updates
   - Shares the folder with all conversation participants via email
   - On subsequent runs, only fetches messages since the last export

## Folder Structure

Each conversation gets its own folder in Google Drive, named with the conversation's display name:
- Channels: Uses the channel name (e.g., `team-orange`)
- DMs: Uses the other participant's name (e.g., `John Doe`)
- Group chats: Uses comma-separated participant names (e.g., `John Doe, Jane Smith`)

All participants are automatically added as viewers to the folder, allowing them to access the conversation history.

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
5. Shares the folder with participants (duplicate shares are handled gracefully)

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

## Contributing

Contributions are welcome! Please open an issue or submit a pull request.
