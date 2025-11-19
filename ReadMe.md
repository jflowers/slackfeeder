# Slack Feeder

This project exports conversations from Slack, processes them into a human-readable format, and uploads them to Google Drive. This allows for easy sharing and consumption of Slack conversations by other tools, such as Gemini.

## Features

- Export conversation history from public channels, private channels, DMs, and group chats
- Processes Slack's JSON export into a clean, readable text format with human-readable timestamps
- Creates Google Docs (instead of text files) optimized for AI tools like Gemini
- Organizes conversations in Google Drive folders named with display names
- Creates one Google Doc per day with format: `channel name slack messages yyyymmdd`
- Automatically shares folders with all conversation participants and keeps access synchronized with channel membership
- Handles incremental updates by appending to existing daily docs (no duplicate headers)
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

**Option 1: Using pip with pyproject.toml (Recommended)**

```bash
# Install runtime dependencies
pip install .

# Or install with development dependencies (includes black, pylint, pytest)
pip install -e ".[dev]"
```

**Option 2: Using requirements.txt (Legacy support)**

```bash
pip install -r requirements.txt
```

**Note:** This project uses `pyproject.toml` (PEP 621) as the **source of truth** for dependencies. The `requirements.txt` file is auto-generated from `pyproject.toml` for backward compatibility. GitHub Dependabot supports `pyproject.toml` for automated dependency updates.

**To update requirements.txt after changing pyproject.toml:**

You have two options:

1. **Use the helper script** (recommended):
   ```bash
   ./scripts/update_requirements.sh
   ```

2. **Manual sync**: After installing from `pyproject.toml`, export dependencies:
   ```bash
   pip install .
   pip freeze | grep -E "(cachetools|google|slack|requests|python-dotenv)" > requirements.txt
   ```

**Important:** Always edit dependencies in `pyproject.toml` first, then regenerate `requirements.txt`. The `pyproject.toml` file is the single source of truth.

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

- **`GOOGLE_DRIVE_TOKEN_FILE`**: (Optional) Path to store OAuth token file
  - Defaults to `~/.config/slackfeeder/token.json` on local machines
  - **For CI/CD:** You'll need to create this token file locally first (see "Running in CI/CD" section)
  - The token file contains OAuth credentials and must have permissions 600 (owner read/write only)

```bash
export SLACK_BOT_TOKEN="xoxb-your-token-here"
export GOOGLE_DRIVE_CREDENTIALS_FILE="./path/to/credentials.json"
export GOOGLE_DRIVE_FOLDER_ID="your-folder-id"
```

**For GitLab CI/CD:**
Set these as CI/CD variables in your GitLab project settings under Settings ? CI/CD ? Variables.

**Important for CI/CD:** Before running in CI/CD, you must set up Google Drive authentication locally to create a token file. See the "Running in CI/CD" section below for detailed instructions.

**For other CI/CD systems:**
Set these as environment variables in your build/deployment configuration.

**Note:** If you use a `.env` file for local development, it will be automatically loaded. Environment variables set directly in your shell will override values in `.env`.

## Usage

### 0. Set Up Google Drive Authentication (First Time Only)

If you plan to use `--upload-to-drive`, you need to authorize Google Drive access first:

```bash
# Set your credentials file path
export GOOGLE_DRIVE_CREDENTIALS_FILE="./path/to/credentials.json"

# Run the setup command (this will open a browser for authorization)
python src/main.py --setup-drive-auth
```

This creates a token file that allows the script to access Google Drive without requiring interactive authorization on subsequent runs. **For CI/CD setup, see the "Running in CI/CD" section below.**

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
        },
        {
            "id": "C06ABC12345",
            "displayName": "selective-channel",
            "export": true,
            "share": true,
            "shareMembers": [
                "U1234567890",
                "alice@example.com",
                "Bob Smith"
            ]
        }
    ]
}
```

- If `export` is not specified, it defaults to `true` (will be exported)
- If `share` is not specified, it defaults to `true` (folder will be shared with participants)
- Set `"share": false"` to export the conversation but not share the folder with participants
- Set `"shareMembers"` to an array of user identifiers to share with only specific members:
  - Can contain Slack user IDs (e.g., `"U1234567890"`)
  - Can contain email addresses (e.g., `"alice@example.com"`)
  - Can contain display names (e.g., `"Bob Smith"`)
  - Matching is case-insensitive
  - If `shareMembers` is not provided, shares with all channel members (backward compatible)
- If `displayName` is not provided, the script will automatically fetch it from Slack (for channels) or construct it from participant names (for DMs and group chats)

**Note:** `people.json` is optional but recommended - it speeds up processing by avoiding API lookups for known users. The system will automatically look up new users on-demand if they're not in the cache.

**Opting Out of Notifications:** If someone wants to receive folder access but not email notifications, you can add `"noNotifications": true` to their entry in `people.json`. They will still get folder access, but won't receive email notifications when folders are shared.

**Opting Out of Sharing:** If someone doesn't want to be shared with at all (no folder access), you can add `"noShare": true` to their entry in `people.json`. They will be completely excluded from folder sharing, even if they're a member of the channel.

Example:

```json
{
    "people": [
        {
            "slackId": "U1234567890",
            "email": "user@example.com",
            "displayName": "John Doe",
            "noNotifications": true
        },
        {
            "slackId": "U0987654321",
            "email": "noshare@example.com",
            "displayName": "Jane Smith",
            "noShare": true
        }
    ]
}
```

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

### Bulk Export for Large Time Periods

For exporting very large time periods (e.g., 2+ years), use the `--bulk-export` flag. This mode:

- **Overrides all limits** (date range, message count, file size)
- **When uploading to Drive**: Creates daily Google Docs (same as normal mode) - one doc per day
- **When NOT uploading to Drive**: Automatically chunks exports into monthly text files when:
  - Date range exceeds 30 days, OR
  - Message count exceeds 10,000 messages
- **Monthly files** (local only) named like: `channel_name_history_2023-01_2025-11-06_14-30-45.txt`
- **Daily Google Docs** (when uploading) named like: `channel name slack messages 20230115`

**Example: Export 2 years of messages**

```bash
python src/main.py --export-history --upload-to-drive --bulk-export --start-date "2023-01-01" --end-date "2024-12-31"
```

When uploading to Drive, this creates daily Google Docs (one per day), making it easy for AI tools like Gemini to query specific dates. When exporting locally without `--upload-to-drive`, it creates monthly text files.

**When to use bulk export:**
- Exporting 2+ years of history
- Exporting channels with >10,000 messages
- When you want to override default safety limits

**When NOT to use bulk export:**
- Regular weekly/monthly incremental exports (use normal mode)
- Small date ranges (<30 days)
- Channels with <10,000 messages

**Note:** When using `--upload-to-drive`, bulk export creates daily Google Docs (same as normal mode). The bulk export flag is mainly useful for overriding limits and for local file exports where monthly chunking is applied.

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

## Browser-Based DM Export (No Slack App Required)

**Note:** This feature is experimental and requires manual browser setup. It cannot run in CI/CD pipelines but provides an alternative way to export DMs without creating a Slack app.

### Overview

The browser-based export method allows you to export Slack DMs by:
1. Opening Slack in a browser (already logged in)
2. Using chrome-devtools MCP server to capture network requests
3. Processing captured API responses into export files

This method doesn't require a Slack bot token or app setup - it uses your existing browser session.

### Prerequisites

- Chrome or Chromium browser with DevTools Protocol access
- chrome-devtools MCP server configured (for automated capture)
- OR manual capture of API responses (see below)

### Method 1: Automated Capture (with chrome-devtools MCP)

If you have chrome-devtools MCP server configured:

1. **Open Slack DM in browser** - Navigate to the DM conversation you want to export
2. **Select the browser page** in chrome-devtools MCP (if needed)
3. **Run the capture script** - The script will automatically scroll through the conversation and capture API responses:
   ```bash
   python scripts/browser_export_dm.py --capture-only --response-dir browser_exports/api_responses --scroll-attempts 50
   ```
   
   **No manual scrolling required!** The script uses JavaScript to programmatically scroll the page.
   If JavaScript scrolling doesn't work, you can use keyboard scrolling with `--use-keyboard-scroll`.
   
   Or use Cursor's MCP tools directly (see below)
4. **Process and upload to Google Drive**:
   ```bash
   python src/main.py --browser-export-dm --upload-to-drive \
     --browser-response-dir browser_exports/api_responses \
     --browser-conversation-name "Tara"
   ```
   Or process locally without Google Drive:
   ```bash
   python src/main.py --browser-export-dm \
     --browser-response-dir browser_exports/api_responses \
     --browser-output-dir slack_exports \
     --browser-conversation-name "Tara"
   ```

### Method 2: Manual Capture (without MCP)

If you don't have MCP server access, you can manually capture API responses:

1. **Open Slack DM in browser** - Navigate to the DM conversation
2. **Open browser DevTools** - Press F12 or right-click → Inspect
3. **Go to Network tab** - Filter for "conversations.history"
4. **Scroll through conversation** - As you scroll up, Slack makes API calls
5. **Save API responses**:
   - Right-click on each `conversations.history` request
   - Select "Copy" → "Copy response"
   - Save to `browser_exports/api_responses/response_0.json`, `response_1.json`, etc.
6. **Process captured responses**:
   ```bash
   python src/main.py --browser-export-dm --browser-response-dir browser_exports/api_responses --browser-output-dir slack_exports --browser-conversation-name "Tara"
   ```

### Command-Line Options

```bash
python src/main.py --browser-export-dm [OPTIONS]
```

**Options:**
- `--browser-response-dir DIR` - Directory containing captured API responses (default: `browser_exports/api_responses`)
- `--browser-output-dir DIR` - Directory to write export files when not using `--upload-to-drive` (default: `slack_exports`)
- `--browser-conversation-name NAME` - Name for the conversation (used in filenames and folder name, default: `DM`)
- `--browser-conversation-id ID` - Optional conversation ID for metadata
- `--start-date DATE` - Filter messages from this date (YYYY-MM-DD format, optional)
- `--end-date DATE` - Filter messages until this date (YYYY-MM-DD format, optional)
- `--upload-to-drive` - Upload to Google Drive as Google Docs (same format as Slack App export)

**Date Range Filtering:**
You can filter messages by date range when processing captured responses:
```bash
# Export only messages from October 2024
python src/main.py --browser-export-dm \
  --browser-response-dir browser_exports/api_responses \
  --browser-conversation-name "Tara" \
  --start-date "2024-10-01" \
  --end-date "2024-10-31"
```

The date filtering happens **after** capture, so you can capture all messages once and then filter them multiple times with different date ranges.

**Google Drive Integration:**
When using `--upload-to-drive`, the browser export:
- Creates Google Docs with the same naming convention: `{conversation_name} slack messages {yyyymmdd}`
- Uses the same format as Slack App export (thread grouping, timestamps, etc.)
- Creates folders named with the conversation name
- Supports incremental updates (appends to existing docs)
- Saves export metadata for future incremental exports
- Requires `GOOGLE_DRIVE_CREDENTIALS_FILE` environment variable

### Output Format

**Local Files (without `--upload-to-drive`):**
Export files are created with format: `YYYY-MM-DD-{conversation_name}.txt`

Example: `2024-10-18-Tara.txt`

Each file contains messages from that date, formatted with:
- User names and timestamps
- Message text
- Reactions, attachments, and files
- Date headers

**Google Drive (with `--upload-to-drive`):**
Google Docs are created with format: `{conversation_name} slack messages {yyyymmdd}`

Example: `Tara slack messages 20241018`

Each Google Doc contains:
- Metadata header (for new docs): Channel name, export date, message count
- Messages formatted exactly like Slack App export:
  - Thread grouping
  - Timestamps in format: `[YYYY-MM-DD HH:MM:SS UTC]`
  - User names (or IDs if not mapped)
  - Thread replies with indentation
- Same format as `--export-history --upload-to-drive` for consistency

### Limitations

- **Requires manual browser setup** - Cannot be automated in CI/CD
- **DM only** - Currently supports DMs, not channels or group chats
- **Automated scrolling** - Uses JavaScript to scroll programmatically (no manual scrolling needed)
- **Session-dependent** - API tokens are session-specific and cannot be reused
- **Rate limiting** - Must respect Slack's rate limits (automatic with automated scrolling)

### Tips

1. **Scroll in batches** - Scroll up, wait for API calls, capture them, then scroll more
2. **Check `has_more` field** - If `has_more: false` in a response, you've reached the beginning
3. **Deduplication** - The processor automatically deduplicates messages using timestamps
4. **Incremental processing** - You can process responses multiple times as you capture more

### Troubleshooting

**No messages in response:**
- Some API calls return empty `messages` arrays (they're checking for updates)
- Only process responses that have actual messages

**Missing messages:**
- Make sure you're scrolling enough to trigger API calls
- Check that responses have `has_more: true` if you expect more messages
- Verify the `oldest` timestamp to see how far back you've gone

**Duplicate messages:**
- The processor uses timestamps for deduplication
- If you see duplicates, check that timestamps are unique

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
   - Groups messages by date (YYYYMMDD format) and creates one Google Doc per day
   - Creates Google Docs with format: `channel name slack messages yyyymmdd`
   - For new docs: Adds full metadata header with channel info and date
   - For existing docs: Appends new messages with separator (no duplicate headers)
   - Automatically tracks the last export date for incremental updates:
     - Creates/updates a metadata file (`{channel_name}_last_export.json`) in each folder
     - Stores the timestamp of the latest message exported
     - Used by subsequent runs to determine what's new
   - Manages folder permissions to keep access synchronized with channel membership:
     - Checks existing folder permissions before sharing
     - If user already has access, skips the share API call (prevents duplicate notifications)
     - Only shares with users who don't already have access
     - Automatically revokes access for users who are no longer channel members
     - Users receive email notifications only on first share
   - Shares the folder with all current conversation participants via email
   - On subsequent runs, only fetches messages since the last export

## Folder Structure

Each conversation gets its own folder in Google Drive, named with the conversation's display name:
- Channels: Uses the channel name (e.g., `team-orange`)
- DMs: Uses the other participant's name (e.g., `John Doe`)
- Group chats: Uses comma-separated participant names (e.g., `John Doe, Jane Smith`)

**Daily Google Docs:** When uploading to Drive, messages are grouped by date and one Google Doc is created per day:
- Format: `channel name slack messages yyyymmdd`
- Example: `team-orange slack messages 20250115`
- Each doc contains all messages from that specific day
- New messages are appended to existing daily docs (no duplicate headers)
- Optimized for AI tools like Gemini that work better with daily files

**Local File Exports:** When not using `--upload-to-drive`, files are saved locally as text files with timestamps (e.g., `channel_name_history_2025-01-15_14-30-45.txt`).

All current participants are automatically added as viewers to the folder, allowing them to access the conversation history. When members are added or removed from a channel, folder access is automatically updated on the next export run.

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
- **Daily Google Docs**: When uploading to Drive, messages are grouped by date and appended to existing daily docs
  - Format: `channel name slack messages yyyymmdd`
  - New messages are appended to the appropriate daily doc (no duplicate headers)
  - Each day gets its own Google Doc, making it easy for AI tools to query specific dates
- **Local files**: When not using `--upload-to-drive`, files are saved locally with timestamps (e.g., `channel_name_history_2024-01-15_14-30-45.txt`)
- **State management**: The script stores the last export timestamp in a small metadata file (`{channel_name}_last_export.json`) **in Google Drive**, not locally
- **CI/CD friendly**: Since state is stored in Drive, the script works perfectly in ephemeral CI/CD environments
- **Manual date ranges**: You can still use `--start-date` and `--end-date` to override the automatic incremental behavior

When you run the script weekly:
1. It checks Google Drive for the last export timestamp (from a metadata file in each folder)
2. Only fetches messages newer than that timestamp (unless `--start-date` is explicitly provided)
3. Groups new messages by date and appends them to the appropriate daily Google Doc
4. Creates new daily docs for dates that don't exist yet
5. Saves the latest message timestamp to a metadata file in Drive (for next run)
6. Manages folder permissions to keep access synchronized with channel membership:
   - If participants already have access, no additional notifications are sent
   - Only new participants receive email notifications
   - Automatically revokes access for users who are no longer channel members
   - This prevents duplicate notifications on subsequent runs

**What Participants See:**
- **First share:** Participants receive an email notification from Google Drive that they've been given access to a folder
- **Subsequent runs:** No notifications sent (permissions already exist)
- **Folder contents:** Daily Google Docs accumulate in the folder, one per day, creating a complete history over time
- **Daily organization:** Each day's messages are in a separate Google Doc, making it easy to find and query specific dates

This means you can share the folder with participants, and they'll see daily Google Docs accumulating over time. The script works identically whether run locally or in CI/CD - no state persistence between runs is required.

## Running in CI/CD

This project is designed to run in CI/CD pipelines. However, Google Drive OAuth requires an initial authorization step that must be done locally before the token can be used in CI/CD.

### Setting Up Google Drive Authentication for CI/CD

**Step 1: Create the token file locally**

Before running in CI/CD, you must authorize Google Drive access on your local machine to create a token file:

```bash
# Set your credentials file path
export GOOGLE_DRIVE_CREDENTIALS_FILE="./path/to/credentials.json"

# Run the setup command (this will open a browser for authorization)
python src/main.py --setup-drive-auth
```

This will:
- Open a browser window for Google OAuth authorization
- Create a token file at `~/.config/slackfeeder/token.json` (or path specified by `GOOGLE_DRIVE_TOKEN_FILE`)
- Set secure file permissions (600) on the token file

**Step 2: Add token file to GitLab CI/CD**

1. Copy the contents of the token file created in Step 1:
   ```bash
   cat ~/.config/slackfeeder/token.json
   ```

2. In GitLab, go to your project ? Settings ? CI/CD ? Variables

3. Add a new variable:
   - **Key:** `GOOGLE_DRIVE_TOKEN_FILE` (or any name you prefer)
   - **Value:** Paste the entire contents of the token file
   - **Type:** Select "File" (this creates a temporary file in CI/CD)
   - **Protected:** Note: File type variables cannot be protected variables in GitLab. To secure the token, use repository-level protection instead (see Security Notes below)
   - **Masked:** Optional (recommended for security)

**Important Security Note:** GitLab file type variables cannot be marked as "Protected" variables. To secure your token file, use repository-level settings:
   - Go to Settings ? CI/CD ? Variables
   - Set "Minimum role to use pipeline variables" to "Owner" (or appropriate role)
   - This restricts who can access CI/CD variables, including your token file
   - Consider also enabling "Protect variable" for other sensitive variables (like `SLACK_BOT_TOKEN`)

**Step 3: Configure GitLab CI/CD**

Example GitLab CI configuration:

```yaml
slack-export:
  image: python:3.11
  script:
    # Install dependencies from pyproject.toml
    - pip install .
    # Set secure permissions on token file (required)
    - chmod 600 "${GOOGLE_DRIVE_TOKEN_FILE}"
    # Set the token file path environment variable
    - export GOOGLE_DRIVE_TOKEN_FILE="${GOOGLE_DRIVE_TOKEN_FILE}"
    - python src/main.py --export-history --upload-to-drive
  only:
    - schedules  # Run on schedule
```

**Note:** You can also use `pip install -r requirements.txt` if you prefer the legacy approach.

**Important:** The `chmod 600` command is required because the script enforces secure file permissions on the token file. GitLab CI/CD file variables are created with default permissions that don't meet the security requirements.

**Step 4: Set other required environment variables**

In GitLab CI/CD settings, also set:
- `SLACK_BOT_TOKEN` - Your Slack bot token
- `GOOGLE_DRIVE_CREDENTIALS_FILE` - Path to credentials JSON file (can be another file variable)
- `GOOGLE_DRIVE_FOLDER_ID` - (Optional) Target folder ID

**For other CI/CD systems:**

The same workflow applies:
1. Run `--setup-drive-auth` locally to create the token file
2. Add the token file contents as a secure variable/file in your CI/CD system
3. Set `GOOGLE_DRIVE_TOKEN_FILE` to point to that file
4. Ensure the token file has permissions 600 before running the script

**Security Notes:**
- The token file contains OAuth credentials - treat it as a secret
- Never commit the token file to version control
- The token file must have permissions 600 (owner read/write only)
- Tokens can be refreshed automatically, but if revoked, you'll need to run `--setup-drive-auth` again

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
- **Large Conversations:** Very large conversations (>50,000 messages) are limited by `MAX_MESSAGES_PER_CONVERSATION` (use `--bulk-export` to override)
- **Date Range:** Maximum date range is limited to 365 days by default (configurable via `MAX_DATE_RANGE_DAYS`, or use `--bulk-export` to override)
- **File Size:** Individual export files are limited to 100MB by default (configurable via `MAX_EXPORT_FILE_SIZE_MB`, or use `--bulk-export` to override)
- **Bot Permissions:** The bot must be a member of channels/groups to export them. Private channels require the bot to be invited.

**Note:** All limits can be overridden using the `--bulk-export` flag, which also enables automatic monthly chunking for large exports.

## Performance Notes

- **Rate Limiting:** The script includes built-in rate limiting for both Slack and Google Drive APIs
- **Caching:** User information is cached using `people.json` to minimize API calls
- **Incremental Exports:** Only fetches new messages, significantly reducing API calls on subsequent runs
- **Batch Processing:** Processes conversations sequentially with small delays to avoid rate limits
- **API Efficiency:** Permission checks prevent unnecessary share API calls

## FAQ

**Q: Will participants get notified every time I run the script?**
A: No. Participants receive email notifications only on the first share. Subsequent runs check permissions and skip sharing if the user already has access. If someone is removed from a channel, their access is automatically revoked (no notification sent), and if someone is added, they automatically receive access (with notification on first share).

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

**Q: Can I share with only specific members of a channel?**
A: Yes! Add a `"shareMembers"` array to the channel configuration. You can specify:
- Slack user IDs (e.g., `"U1234567890"`)
- Email addresses (e.g., `"alice@example.com"`)
- Display names (e.g., `"Bob Smith"`)

Example:
```json
{
    "id": "C06ABC12345",
    "displayName": "team-channel",
    "export": true,
    "share": true,
    "shareMembers": ["U1234567890", "alice@example.com", "Bob Smith"]
}
```

Matching is case-insensitive, and you can mix different identifier types in the same list. If `shareMembers` is not provided, the folder is shared with all channel members (backward compatible).

**Q: What happens when someone is added or removed from a private channel?**
A: Folder access is automatically synchronized with channel membership on each export run. When someone is added to a channel, they automatically receive folder access (with email notification on first share). When someone is removed from a channel, their folder access is automatically revoked (no notification sent). This ensures only current channel members have access to the exported conversation history.

**Q: Can I export archived channels?**
A: The script skips archived channels by default. To export them, you would need to modify the code.

**Q: How do I export 2+ years of messages?**
A: Use the `--bulk-export` flag along with your date range. When uploading to Drive, this creates daily Google Docs (one per day):
```bash
python src/main.py --export-history --upload-to-drive --bulk-export --start-date "2023-01-01" --end-date "2024-12-31"
```

This creates Google Docs named like `channel name slack messages 20230101`, `channel name slack messages 20230102`, etc., making it easy for AI tools like Gemini to query specific dates.

**Q: What does bulk export do?**
A: Bulk export mode:
- Overrides all safety limits (date range, message count, file size)
- When uploading to Drive: Creates daily Google Docs (same as normal mode)
- When exporting locally: Automatically chunks large exports into monthly text files
- Makes it easier for AI tools to query specific time periods

**Q: When should I use bulk export vs regular export?**
A: Use bulk export for:
- Exporting 2+ years of history
- Very large channels (>10,000 messages)
- When you need monthly chunks for easier querying

Use regular export for:
- Weekly/monthly incremental updates
- Small date ranges (<30 days)
- Normal-sized channels

## Development

### Code Formatting and Linting

This project uses modern Python tooling for code quality:

- **Black**: Code formatter (line length: 100)
- **isort**: Import sorting (compatible with Black)
- **pylint**: Code linting

**Format code:**
```bash
black src/ tests/
isort src/ tests/
```

**Lint code:**
```bash
pylint src/ tests/
```

**Run all checks:**
```bash
black --check src/ tests/
isort --check src/ tests/
pylint src/ tests/
```

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src --cov-report=html
```

### Dependencies

Dependencies are managed in `pyproject.toml` (PEP 621 format), which is supported by:
- GitHub Dependabot (automated dependency updates)
- Modern pip (>=21.0)
- Poetry, pipenv, and other modern tools

The `requirements.txt` file should be kept in sync with `pyproject.toml` for backward compatibility. **Always edit dependencies in `pyproject.toml` first**, then regenerate `requirements.txt` using the helper script:
```bash
./scripts/update_requirements.sh
```

Or use `make requirements` if you have Make installed.

This ensures both files stay in sync. The `pyproject.toml` file is the single source of truth.

## Contributing

Contributions are welcome! Please open an issue or submit a pull request.

Before submitting:
1. Format code with `black` and `isort`
2. Run `pylint` and fix any issues
3. Ensure all tests pass with `pytest`
