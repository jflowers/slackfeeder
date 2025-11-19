# Browser-Based DM Export Feature

## Overview

This feature allows you to export Slack DMs without requiring a Slack app or bot token. It works by capturing network requests from a browser session where you're already logged into Slack.

## Architecture

### Components

1. **`src/browser_scraper.py`** - Browser control and network request capture
   - `BrowserScraper` class for managing browser sessions
   - Functions to extract messages and metadata from API responses
   - Network request filtering utilities

2. **`src/browser_response_processor.py`** - Process captured API responses
   - `BrowserResponseProcessor` class for processing responses
   - Message extraction, deduplication, and formatting
   - Date-based grouping and file generation

3. **`scripts/browser_export_dm.py`** - Standalone script for browser export
   - Can be used with chrome-devtools MCP server for automated capture
   - Can process manually captured responses
   - Command-line interface for both capture and processing

4. **`src/main.py`** - Integration with main application
   - `--browser-export-dm` flag for browser export mode
   - Processes captured responses into export format
   - Integrated with existing project structure

## Usage

### Method 1: Using Main Application

1. **Capture API responses** (manually or with MCP):
   - Open Slack DM in browser
   - Scroll through conversation to trigger API calls
   - Capture `conversations.history` responses
   - Save to `browser_exports/api_responses/response_*.json`

2. **Process responses**:
   ```bash
   python src/main.py --browser-export-dm \
     --browser-response-dir browser_exports/api_responses \
     --browser-output-dir slack_exports \
     --browser-conversation-name "Tara"
   ```

### Method 2: Using Standalone Script

```bash
# Process only (if you already have captured responses)
python scripts/browser_export_dm.py --process-only \
  --response-dir browser_exports/api_responses \
  --output-dir slack_exports \
  --conversation-name "Tara"

# Capture and process (requires MCP tools)
python scripts/browser_export_dm.py \
  --response-dir browser_exports/api_responses \
  --output-dir slack_exports \
  --conversation-name "Tara" \
  --scroll-attempts 50
```

## Manual Capture Process

If you don't have chrome-devtools MCP server:

1. **Open Slack DM** in browser
2. **Open DevTools** (F12)
3. **Go to Network tab**
4. **Filter for "conversations.history"**
5. **Scroll up** in the conversation
6. **Right-click each request** → Copy → Copy response
7. **Save to JSON files** in `browser_exports/api_responses/`:
   - `response_0.json`
   - `response_1.json`
   - etc.

## Output Format

Files are created with format: `YYYY-MM-DD-{conversation_name}.txt`

Example: `2024-10-18-Tara.txt`

Each file contains:
- Date header: `## Friday, October 18, 2024`
- Messages with user names, timestamps, and text
- Reactions, attachments, and files (if present)

## User ID Mapping

By default, user IDs are shown as-is (e.g., `U02PHQFTBC6`). To map IDs to names:

1. Create a JSON file with user mappings:
   ```json
   {
     "U02PHQFTBC6": "Tara",
     "UUR9FNZ88": "Jay Flowers"
   }
   ```

2. Use with the script:
   ```bash
   python scripts/browser_export_dm.py --process-only \
     --user-map user_map.json \
     --response-dir browser_exports/api_responses \
     --output-dir slack_exports
   ```

## Limitations

- **DM only** - Currently supports DMs, not channels or group chats
- **Manual setup** - Requires browser session and manual/MCP capture
- **Not CI/CD compatible** - Cannot run in automated pipelines
- **Session-dependent** - API tokens are session-specific
- **Rate limiting** - Must respect Slack's rate limits

## Testing

Run tests with:
```bash
pytest tests/test_browser_scraper.py -v
```

All 21 tests should pass, covering:
- Message extraction from API responses
- Metadata extraction
- Network request filtering
- User ID discovery
- Message formatting
- Date grouping
- Deduplication
- File processing

## Future Enhancements

Potential improvements:
- Automatic user ID to name mapping via Slack API (if token available)
- Support for channels and group chats
- Integration with Google Drive upload (like main export)
- Better MCP integration for automated capture
- Progress tracking for long conversations

## Files Created

- `src/browser_scraper.py` - Browser scraper module
- `src/browser_response_processor.py` - Response processor module
- `scripts/browser_export_dm.py` - Standalone script
- `tests/test_browser_scraper.py` - Test suite
- `ReadMe.md` - Updated with browser export documentation
- `BROWSER_EXPORT.md` - This file

## Integration with Existing Code

The browser export feature:
- Uses existing utility functions (`sanitize_filename`, `setup_logging`)
- Follows project conventions (logging, error handling)
- Outputs in similar format to main export
- Can be extended to integrate with Google Drive upload

## Notes

- This feature is marked as experimental in the documentation
- It's designed for manual use, not CI/CD
- The processing logic is stable and well-tested
- Browser capture requires manual setup or MCP server configuration
