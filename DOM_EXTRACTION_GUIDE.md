# DOM Extraction Guide for Cursor Agent

This guide explains how to successfully extract messages from Slack DOM using Cursor's MCP chrome-devtools tools. This is the **working method** that has been proven successful in multiple sessions.

## ⚠️ Important: Use MCP Tools Directly

**Do NOT create temporary wrapper scripts** for DOM extraction. Previous attempts created scripts like `extract_tara_2025.py` and `run_extraction.py`, but these have been removed as they are unnecessary.

**Instead:** Use MCP tools directly as documented below. The workflow is:
1. Use `mcp_chrome-devtools_press_key` to scroll
2. Use `mcp_chrome-devtools_evaluate_script` to extract
3. Use `scripts/combine_messages.py` to combine
4. Use `src/main.py` to process and upload

No wrapper scripts are needed - the MCP tools are sufficient.

## Overview

The DOM extraction process works by:
1. **Scrolling** through Slack conversation using MCP tools
2. **Extracting** messages from the visible DOM using JavaScript
3. **Combining** new messages with existing ones using `combine_messages.py`
4. **Processing** and uploading to Google Drive using `src/main.py`

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
   # Press PageUp multiple times to load older messages
   mcp_chrome-devtools_press_key(key="PageUp")
   # Repeat 5-10 times, then wait
   sleep(3)  # Wait for messages to load
   ```

2. **Extract messages** using `mcp_chrome-devtools_evaluate_script`:
   ```javascript
   // Use the JavaScript from src/browser_scraper.py
   // This extracts messages from the DOM
   ```

3. **Combine new messages** using `scripts/combine_messages.py`:
   ```bash
   python3 scripts/combine_messages.py '{"ok":true,"messages":[...]}'
   ```

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

## combine_messages.py Usage

The `scripts/combine_messages.py` script combines new messages with existing ones:

```bash
python3 scripts/combine_messages.py '{"ok":true,"messages":[...]}'
```

**What it does:**
- Loads existing messages from `browser_exports/response_dom_extraction.json`
- Adds new messages (deduplicates by timestamp)
- Sorts all messages by timestamp
- Saves back to `browser_exports/response_dom_extraction.json`

**Output:**
```
Added 10 new messages
Total: 252 messages
Date range: 2023-11-29 15:31:11.457439 to 2024-06-03 14:59:31.436389
```

## Example: Complete Extraction Session

Here's what a successful session looks like:

1. **User request:**
   > "Please extract messages from the DOM for Tara from January 3rd 2024 to June 5th 2024 and upload to Google Drive"

2. **Agent actions:**
   - Scrolls backward using `PageUp` keys
   - Extracts messages using JavaScript evaluation
   - Combines messages using `combine_messages.py`
   - Repeats until target date range is covered
   - Processes and uploads using `src/main.py`

3. **Result:**
   - Messages extracted and saved to `browser_exports/response_dom_extraction.json`
   - Google Docs created in Google Drive folder "Tara"
   - One doc per day with messages

## Tips for Success

1. **Scroll gradually:** Press `PageUp` 5-10 times, wait 3 seconds, then extract
2. **Check progress:** After combining, check the date range to see how far back you've gone
3. **Handle gaps:** If you notice gaps (e.g., Dec 1-13 missing), scroll to that range and extract
4. **Deduplication:** `combine_messages.py` automatically deduplicates, so you can extract overlapping ranges safely
5. **Date filtering:** Use `--start-date` and `--end-date` in `src/main.py` to process only specific ranges

## Troubleshooting

**Problem:** Extraction returns 0 messages
- **Solution:** Make sure browser is focused and Slack conversation is visible. Try scrolling a bit more.

**Problem:** Messages not combining properly
- **Solution:** Check that JSON is properly escaped when passing to `combine_messages.py`. Use single quotes around the JSON string.

**Problem:** Date range not reached
- **Solution:** Continue scrolling backward. Slack uses virtual scrolling, so you need to scroll through all intermediate dates.

## Files Involved

- `browser_exports/response_dom_extraction.json` - Main extraction file (accumulates messages)
- `scripts/combine_messages.py` - Combines new messages with existing ones
- `src/browser_scraper.py` - Contains JavaScript extraction function
- `src/main.py` - Processes and uploads to Google Drive
