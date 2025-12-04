"""
Command-line interface and argument parsing for Slack Feeder.
"""
import argparse
import os
from typing import Optional

# Configuration file names
BROWSER_EXPORT_CONFIG_KEY = "browser-export"  # Key in browser-export.json
BROWSER_EXPORT_CONFIG_FILENAME = "browser-export.json"  # Default config filename
CHANNELS_CONFIG_FILENAME = "channels.json"  # Channels config filename
PEOPLE_CONFIG_FILENAME = "people.json"  # People cache filename
METADATA_FILE_SUFFIX = "_last_export.json"  # Suffix for metadata files in Google Drive


def parse_arguments() -> argparse.Namespace:
    """Parse command-line arguments.
    
    Returns:
        argparse.Namespace with parsed arguments
    """
    parser = argparse.ArgumentParser(
        description="Export Slack conversations and upload to Google Drive."
    )
    parser.add_argument(
        "--make-ref-files",
        action="store_true",
        help="Generate reference files (channels.json, people.json).",
    )
    parser.add_argument(
        "--export-history", action="store_true", help="Export conversation history."
    )
    parser.add_argument(
        "--upload-to-drive", action="store_true", help="Upload exported files to Google Drive."
    )
    parser.add_argument(
        "--setup-drive-auth",
        action="store_true",
        help="Set up Google Drive authentication and create token file for CI/CD. Run this once locally before using in CI/CD.",
    )
    parser.add_argument("--start-date", help="Start date for history export (YYYY-MM-DD).")
    parser.add_argument("--end-date", help="End date for history export (YYYY-MM-DD).")
    parser.add_argument(
        "--bulk-export",
        action="store_true",
        help="Enable bulk export mode: overrides limits and automatically chunks large exports into monthly files.",
    )
    parser.add_argument(
        "--browser-export-dm",
        action="store_true",
        help="Export DM using browser-based scraping (requires chrome-devtools MCP and pre-positioned browser).",
    )
    parser.add_argument(
        "--browser-response-dir",
        type=str,
        default="browser_exports",
        help="Directory containing DOM extraction file for browser export (default: browser_exports).",
    )
    parser.add_argument(
        "--browser-output-dir",
        type=str,
        default="output",
        help="Output directory for browser export files (default: output).",
    )
    parser.add_argument(
        "--browser-export-config",
        type=str,
        help=f"Path to {BROWSER_EXPORT_CONFIG_FILENAME} file (required for browser exports).",
    )
    parser.add_argument(
        "--browser-conversation-id",
        type=str,
        help="Conversation ID for browser export (optional if using --browser-export-config).",
    )
    parser.add_argument(
        "--browser-conversation-name",
        type=str,
        help="Conversation name for browser export (optional if using --browser-export-config).",
    )
    parser.add_argument(
        "--select-conversation",
        action="store_true",
        default=True,
        help="Automatically select conversation from sidebar (default: True). Use --no-select-conversation to disable.",
    )
    parser.add_argument(
        "--no-select-conversation",
        dest="select_conversation",
        action="store_false",
        help="Disable automatic conversation selection from sidebar.",
    )
    parser.add_argument(
        "--extract-active-threads",
        action="store_true",
        help="Extract active threads from browser (requires --browser-export-dm and --upload-to-drive).",
    )
    parser.add_argument(
        "--extract-historical-threads",
        action="store_true",
        help="Extract historical threads via search (requires --browser-export-dm and --upload-to-drive).",
    )
    parser.add_argument(
        "--search-query",
        type=str,
        help="Custom search query for historical thread extraction (default: in:\"conversation_name\" is:thread).",
    )
    # Set default to True after adding both arguments
    parser.set_defaults(select_conversation=True)
    
    args = parser.parse_args()
    
    # Handle setup-drive-auth separately - doesn't require other args
    if args.setup_drive_auth:
        from src.utils import setup_logging, sanitize_path_for_logging
        from src.google_drive import GoogleDriveClient
        import sys
        
        logger = setup_logging()
        google_drive_credentials_file = os.getenv("GOOGLE_DRIVE_CREDENTIALS_FILE", "").strip()
        if not google_drive_credentials_file:
            logger.error(
                "GOOGLE_DRIVE_CREDENTIALS_FILE environment variable is required for --setup-drive-auth. Exiting."
            )
            sys.exit(1)

        try:
            google_drive_credentials_file = os.path.abspath(
                os.path.expanduser(google_drive_credentials_file)
            )
            if not os.path.exists(google_drive_credentials_file):
                logger.error(
                    f"Credentials file not found: {sanitize_path_for_logging(google_drive_credentials_file)}"
                )
                sys.exit(1)
            if not os.path.isfile(google_drive_credentials_file):
                logger.error(
                    f"Credentials path is not a file: {sanitize_path_for_logging(google_drive_credentials_file)}"
                )
                sys.exit(1)
            if not os.access(google_drive_credentials_file, os.R_OK):
                logger.error(
                    f"Credentials file is not readable: {sanitize_path_for_logging(google_drive_credentials_file)}"
                )
                sys.exit(1)
        except (OSError, ValueError) as e:
            logger.error(f"Invalid credentials file path: {sanitize_path_for_logging(str(e))}")
            sys.exit(1)

        try:
            token_path = GoogleDriveClient.setup_authentication(google_drive_credentials_file)
            logger.info("=" * 80)
            logger.info("Google Drive authentication setup complete!")
            logger.info(f"Token file created at: {token_path}")
            logger.info("")
            logger.info("Next steps for CI/CD:")
            logger.info("1. Copy the contents of the token file")
            logger.info("2. Add it as a CI/CD variable (file type) in your GitLab project")
            logger.info("3. Set GOOGLE_DRIVE_TOKEN_FILE in your CI/CD to point to that variable")
            logger.info("4. Add 'chmod 600 \"${GOOGLE_DRIVE_TOKEN_FILE}\"' to your CI/CD script")
            logger.info("=" * 80)
            sys.exit(0)
        except Exception as e:
            logger.error(f"Failed to set up authentication: {e}", exc_info=True)
            sys.exit(1)
    
    return args
