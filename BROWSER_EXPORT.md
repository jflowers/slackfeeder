# Browser-Based DM Export Feature

## Overview

This feature allows you to export Slack DMs without requiring a Slack app or bot token. It works by extracting messages directly from the DOM of a browser session where you're already logged into Slack.

**Why DOM Extraction?** Slack's API responses are often cached client-side, returning empty message arrays even when messages are visible. DOM extraction reads messages directly from the rendered HTML, making it reliable.

## Architecture

### Components

1. **`src/browser_scraper.py`** - Browser control and DOM extraction
   - `extract_messages_from_dom()` function for extracting messages from HTML
   - JavaScript code to parse Slack's DOM structure
   - Message extraction with user names, timestamps, and text

2. **`src/browser_response_processor.py`** - Process extracted messages
   - `BrowserResponseProcessor` class for processing messages
   - Message extraction, deduplication, and formatting
   - Date-based grouping and file generation

3. **`scripts/extract_dom_messages.py`** - Helper script for DOM extraction
   - Uses MCP chrome-devtools tools to extract messages
   - Saves results to `response_dom_extraction.json`
   - Handles deduplication and message combining

4. **`src/main.py`** - Integration with main application
   - `--browser-export-dm` flag for browser export mode
   - Processes extracted messages into export format
   - Integrated with existing project structure

## Usage

### Step-by-Step Process

1. **Open Slack DM in browser** - Navigate to the DM conversation you want to export
2. **Scroll through conversation** - Use PageUp/PageDown keys to load all messages in your date range
   - Scroll backward to load older messages
   - Scroll forward to load newer messages
   - Ensure all messages in your date range are visible
3. **Extract messages from DOM** - Use MCP chrome-devtools tools:
   ```python
   # Option 1: Use the helper script
   python scripts/extract_dom_messages.py
   
   # Option 2: Use MCP tools directly in Cursor
   # Call extract_messages_from_dom() with mcp_chrome-devtools_evaluate_script
   ```
   This saves messages to `browser_exports/api_responses/response_dom_extraction.json`
4. **Process and upload to Google Drive**:
   ```bash
   python src/main.py --browser-export-dm --upload-to-drive \
     --browser-response-dir browser_exports/api_responses \
     --browser-conversation-name "Tara" \
     --start-date 2025-11-01 \
     --end-date 2025-11-18
   ```
   Or process locally without Google Drive:
   ```bash
   python src/main.py --browser-export-dm \
     --browser-response-dir browser_exports/api_responses \
     --browser-output-dir slack_exports \
     --browser-conversation-name "Tara" \
     --start-date 2025-11-01 \
     --end-date 2025-11-18
   ```

### Multiple Extraction Passes

If you need to extract messages from different parts of the conversation:

1. Scroll to first section and extract
2. Scroll to next section and extract again (the script will deduplicate)
3. Continue until all messages are captured
4. Process the combined `response_dom_extraction.json` file

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
- **Manual setup** - Requires browser session and MCP chrome-devtools access
- **Not CI/CD compatible** - Cannot run in automated pipelines
- **Requires scrolling** - Messages must be scrolled into view before extraction
- **DOM-dependent** - Relies on Slack's HTML structure (may break if Slack changes their UI)

## Testing

Run tests with:
```bash
pytest tests/test_browser_scraper.py -v
```

All tests should pass, covering:
- Message extraction from DOM
- Metadata extraction
- User name extraction
- Message formatting
- Date grouping
- Deduplication
- File processing

## Future Enhancements

Potential improvements:
- Automatic scrolling and extraction in a single pass
- Support for channels and group chats
- Better handling of threads and replies
- Progress tracking for long conversations
- Automatic retry on extraction failures

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
