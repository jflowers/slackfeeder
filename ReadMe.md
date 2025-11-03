# Slack Feeder

This project exports conversations from Slack, processes them into a human-readable format, and uploads them to Google Drive. This allows for easy sharing and consumption of Slack conversations by other tools, such as Gemini.

## Features

-   Export conversation history from public and private channels.
-   Processes Slack's JSON export into a clean, readable text format.
-   Creates and organizes conversations in Google Drive.
-   Shares conversations with the original participants.
-   Configuration is separated from the code for security.

## Setup

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/jflowers/slackfeeder.git
    cd slackfeeder
    ```

2.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

3.  **Configure the application:**
    -   Copy `config/config.json.example` to `config/config.json`.
    -   Update `config/config.json` with your Slack bot token and Google Drive credentials.
    -   **Slack Bot Token:** Create a Slack app and grant it the necessary permissions (`channels:history`, `channels:read`, `groups:history`, `groups:read`, `im:history`, `im:read`, `mpim:history`, `mpim:read`, `users:read`, `users:read.email`).
    -   **Google Drive Credentials:** Follow the instructions to create a Google Cloud project and enable the Google Drive API. Download the credentials as a JSON file.

## Usage

### 1. Generate Reference Files

First, you need to generate reference files for the channels and users your bot has access to.

```bash
python src/main.py --make-ref-files
```

This will create `config/channels.json` and `config/people.json`. You should review `config/channels.json` and copy the channels you want to export to `config/conversations.json`.

### 2. Export and Upload

To export the conversations and upload them to Google Drive, run the following command:

```bash
python src/main.py --export-history --upload-to-drive
```

You can also specify a date range for the export:

```bash
python src/main.py --export-history --upload-to-drive --start-date "YYYY-MM-DD" --end-date "YYYY-MM-DD"
```

## How it Works

1.  **`--make-ref-files`**:
    -   The script connects to the Slack API using your bot token.
    -   It fetches a list of all channels the bot is a member of.
    -   It fetches a list of all users in those channels.
    -   It saves this information to `config/channels.json` and `config/people.json`.

2.  **`--export-history`**:
    -   The script reads the list of channels to export from `config/conversations.json`.
    -   For each channel, it fetches the conversation history from the Slack API.
    -   It processes the history, replacing user IDs with display names and formatting the messages into a readable text file.
    -   The processed files are saved in the `slack_exports` directory.

3.  **`--upload-to-drive`**:
    -   The script connects to the Google Drive API.
    -   For each exported conversation, it creates a new folder in the specified Google Drive folder.
    -   It uploads the processed text file to the new folder.
    -   It shares the folder with the participants of the conversation.

## Contributing

Contributions are welcome! Please open an issue or submit a pull request.
