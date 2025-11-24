# DOM Extraction Guide for Cursor Agent

This guide explains how to successfully extract messages from Slack DOM using Cursor's MCP chrome-devtools tools. This is the **working method** that has been proven successful in multiple sessions.

## ⚠️ Important: Use MCP Tools Directly

**Do NOT create temporary wrapper scripts** for DOM extraction. Previous attempts created scripts like `extract_tara_2025.py` and `run_extraction.py`, but these have been removed as they are unnecessary.

**Instead:** Use MCP tools directly as documented below. The workflow is:
1. Use `mcp_chrome-devtools_press_key` to scroll
2. Use `mcp_chrome-devtools_evaluate_script` to extract
3. Use `scripts/extract_dom_messages.py` to combine and deduplicate (outputs to stdout)
4. Pipe directly to `src/main.py` to process and upload

No wrapper scripts are needed - the MCP tools are sufficient.

## Overview

The DOM extraction process works by:
1. **Scrolling** through Slack conversation using MCP tools
2. **Extracting** messages from the visible DOM using JavaScript
3. **Combining and deduplicating** messages using `extract_dom_messages.py` (with `append=True` for incremental extraction)
4. **Piping** messages directly to `src/main.py` for processing and uploading to Google Drive

## Why Not Use `extract_dom_messages.py` Directly?

The `scripts/extract_dom_messages.py` script exists but **cannot be used directly** in Cursor because:
- It requires passing MCP tools as callable functions (`mcp_evaluate_script`, `mcp_press_key`)
- Cursor's MCP tools are not callable Python functions - they're tool calls
- The script was designed for a different environment

**Solution:** Use MCP tools directly and combine messages incrementally.

## Step-by-Step Workflow

### Step 1: Position Browser

1. Open Slack in Chrome with remote debugging enabled
2. Navigate to the DM conversation you want to export
3. Scroll to the starting point (e.g., if extracting from Nov 1st, scroll to Nov 1st)

### Step 2: Extract Messages Incrementally

The Cursor Agent should:

1. **Scroll backward** using `mcp_chrome-devtools_press_key` with `PageUp`:
   ```python
   # Press PageUp a few times to load older messages (less aggressive)
   mcp_chrome-devtools_press_key(key="PageUp")
   sleep(0.3)  # Small delay between presses
   mcp_chrome-devtools_press_key(key="PageUp")
   sleep(3)  # Wait for messages to load
   ```

2. **Extract messages** using `mcp_chrome-devtools_evaluate_script`:
   ```javascript
   // Use the JavaScript from src/browser_scraper.py
   // This extracts messages from the DOM
   ```

3. **Extract and combine messages** using `scripts/extract_dom_messages.py`:
   - Use `append=True` for incremental extraction (combines with previous extractions)
   - Use `output_to_stdout=True` to pipe directly to `main.py`
   - The script handles deduplication and sorting automatically

4. **Repeat** until you've reached the target date or no new messages are found

### Step 3: Process and Upload

Once extraction is complete:

```bash
python src/main.py --browser-export-dm --upload-to-drive \
  --browser-response-dir browser_exports \
  --browser-conversation-name "Tara" \
  --start-date 2023-11-29 \
  --end-date 2024-06-05
```

**⚠️ CRITICAL: `--browser-conversation-name` is REQUIRED**

You **must** specify `--browser-conversation-name` with the actual conversation name (e.g., `"Tara"`). The default "DM" is not allowed and will cause the script to fail. This ensures messages are organized in folders named after the actual conversation, matching the behavior of regular API exports.

## JavaScript Extraction Script

The extraction script is located in `src/browser_scraper.py`:

```javascript
() => {
    const links = document.querySelectorAll('a[href*="/archives/"]');
    const messages = [];
    const seen = new Set();
    
    for (const link of Array.from(links)) {
        const href = link.href;
        const pIndex = href.lastIndexOf('/p');
        if (pIndex === -1) continue;
        const tsStr = href.substring(pIndex + 2);
        if (tsStr.length < 10) continue;
        
        const ts = tsStr.substring(0, 10) + '.' + tsStr.substring(10);
        if (seen.has(ts)) continue;
        seen.add(ts);
        
        const container = link.closest('div[role="presentation"], div');
        if (!container) continue;
        
        let userName = null;
        const buttons = container.querySelectorAll('button');
        for (const btn of buttons) {
            const txt = btn.textContent.trim();
            if (txt && txt.length > 1 && txt !== 'React' && txt !== 'Reply' && 
                txt !== 'More' && txt !== 'Add' && txt.indexOf(':') < 0 &&
                !txt.match(/^\d{1,2}:\d{2}/)) {
                userName = txt;
                break;
            }
        }
        
        let text = container.textContent.trim();
        
        if (userName) {
            const nameIndex = text.indexOf(userName);
            if (nameIndex !== -1) {
                text = text.substring(nameIndex + userName.length).trim();
            }
        }
        
        const timePatterns = [/^\d{1,2}:\d{2}\s+AM/, /^\d{1,2}:\d{2}\s+PM/, /^\d{1,2}:\d{2}/];
        for (const pattern of timePatterns) {
            const match = text.match(pattern);
            if (match) {
                text = text.substring(match[0].length).trim();
                break;
            }
        }
        
        text = text.replace(/React.*/g, '').replace(/Reply.*/g, '').replace(/More.*/g, '');
        text = text.replace(/Add reaction.*/g, '').replace(/\s+/g, ' ').trim();
        
        if (text.length > 0) {
            messages.push({
                ts: ts,
                user: userName || 'unknown',
                text: text,
                type: 'message'
            });
        }
    }
    
    messages.sort((a, b) => parseFloat(a.ts) - parseFloat(b.ts));
    
    return {
        ok: true,
        messages: messages,
        message_count: messages.length,
        oldest: messages.length > 0 ? messages[0].ts : null,
        latest: messages.length > 0 ? messages[messages.length - 1].ts : null
    };
}
```

## Message Combining and Deduplication

The `scripts/extract_dom_messages.py` script handles combining and deduplication automatically:

- **Deduplication**: Automatically deduplicates messages by timestamp
- **Sorting**: Sorts all messages by timestamp
- **Incremental extraction**: Use `append=True` to combine with previous extractions
- **Output**: Use `output_to_stdout=True` to pipe directly to `main.py`

**Example workflow:**
```bash
# Extract messages and pipe directly to main.py
python3 scripts/extract_dom_messages.py \
  --mcp-evaluate-script <function> \
  --mcp-press-key <function> \
  --output-to-stdout | \
  python3 src/main.py --browser-export-dm --browser-conversation-name "Tara" --upload-to-drive
```

## Example: Complete Extraction Session

Here's what a successful session looks like:

1. **User request:**
   > "Please extract messages from the DOM for Tara from January 3rd 2024 to June 5th 2024 and upload to Google Drive"

2. **Agent actions:**
   - Scrolls backward using `PageUp` keys
   - Extracts messages using JavaScript evaluation via `extract_dom_messages.py`
   - Uses `append=True` for incremental extraction (combines and deduplicates automatically)
   - Repeats until target date range is covered
   - Pipes combined messages directly to `src/main.py` via stdout (no intermediate files)

3. **Result:**
   - Messages extracted and piped directly to `main.py` via stdin
   - Google Docs created in Google Drive folder "Tara"
   - One doc per day with messages (same format as `--export-history`)

## Tips for Success

1. **Scroll gradually:** Press `PageUp` 2 times (with 0.3s delay between), wait 3 seconds, then extract
2. **Check progress:** After extraction, check the date range to see how far back you've gone
3. **Use date separators to identify gaps:** Take snapshots and look for date separators (e.g., "Friday, June 6th"). If you see non-consecutive dates (e.g., "June 27th" and "July 7th"), there are no messages between them - skip scrolling through those dates.
4. **Ensure complete day coverage:** When extracting a specific date, scroll until you see both that date's separator and the previous date separator to ensure you've captured all messages from that day.
5. **Handle gaps efficiently:** Use date separators to identify true gaps rather than scrolling through empty date ranges
6. **Deduplication:** `extract_dom_messages.py` automatically deduplicates, so you can extract overlapping ranges safely
7. **Date filtering:** Use `--start-date` and `--end-date` in `src/main.py` to process only specific ranges
8. **Incremental extraction:** Use `append=True` in `extract_dom_messages.py` to combine with previous extractions

## Using Date Separators

Slack displays date separators in the DOM (e.g., "Friday, June 6th Press enter to select a date to jump to.") that are visible in snapshots as `listitem` elements with `roledescription="separator"`.

**Key Benefits:**

1. **Identify True Gaps:** If you see "June 27th" and "July 7th" separators both visible, there are no messages for June 28-30 and July 1-6. Skip scrolling through these dates.

2. **Ensure Complete Day Coverage:** When extracting messages for June 6th:
   - Scroll backward until you see the "June 6th" separator
   - Continue scrolling until you see the previous date separator (e.g., "May 27th")
   - Extract messages - you now have all messages from June 6th

**How to Check Date Separators:**

Use `mcp_chrome-devtools_take_snapshot()` and look for elements like:
```
listitem "Friday, June 6th Press enter to select a date to jump to." level="1" roledescription="separator"
```

The visible date separators tell you:
- What dates have messages (separators are only shown for dates with messages)
- What dates are missing (gaps between visible separators indicate no messages)
- Whether you've scrolled far enough to capture all messages for a given day

## Troubleshooting

**Problem:** Extraction returns 0 messages
- **Solution:** Make sure browser is focused and Slack conversation is visible. Try scrolling a bit more.

**Problem:** Messages not combining properly
- **Solution:** Use `append=True` in `extract_dom_messages.py` for incremental extraction. The script handles combining automatically.

**Problem:** Date range not reached
- **Solution:** Continue scrolling backward. Slack uses virtual scrolling, so you need to scroll through all intermediate dates.

## Files Involved

- `scripts/extract_dom_messages.py` - Extracts messages from DOM, handles deduplication and combining, outputs to stdout
- `src/browser_scraper.py` - Contains JavaScript extraction function
- `src/main.py` - Processes messages from stdin and uploads to Google Drive

**Important:** No intermediate files are created. Messages flow directly via stdin/stdout pipes.
