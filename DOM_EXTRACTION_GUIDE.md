# DOM Extraction Guide for AI Agents

This guide explains how an AI assistant with MCP browser connectivity (e.g., `chrome-devtools`) should perform Slack message extraction. It details the robust DOM selectors and strategies required to reliably scrape messages and date separators from the Slack web client.

## 1. Overview

The goal is to extract message history from a Slack channel or DM without using the Slack API (which requires an app token). Instead, we automate a browser session to scroll through the history and "scrape" the data directly from the DOM.

**Prerequisites:**
*   Active browser session logged into Slack.
*   MCP server for Chrome DevTools connected.
*   The conversation to be exported is currently open in the active tab.

## 2. Core Strategy

The extraction process involves:
1.  **Selection:** Identifying the correct container elements for messages.
2.  **Extraction:** Parsing timestamps, user names, and message content from those elements.
3.  **Navigation:** Scrolling up to load older messages.
4.  **Deduplication:** Handling the virtual list behavior (where items are reused/unmounted) by tracking unique message IDs.

## 3. DOM Structure & Selectors (Robust Method)

Slack uses a "virtual list" to render messages. Elements appear and disappear from the DOM as the user scrolls. Do **not** rely on fragile XPath or text searching. Use the stable data attributes provided by Slack's frontend architecture.

### 3.1. Message Containers

All items in the feed (messages, date separators, system events) share the same container class and attribute:

*   **Selector:** `div[data-qa="virtual-list-item"]`
*   **Key Attribute:** `data-item-key`

This attribute is the source of truth.
*   **Messages:** The key is a float timestamp (e.g., `1754495975.187839`).
*   **Separators/Headers:** The key is usually a composite string (e.g., `1754452800000.C073BC2HHUZ` or `sectionHeading-...`).

### 3.2. Identifying Messages

To confirm an item is a message:
1.  Check `data-item-key` matches the regex `^\d+\.\d+$`.
2.  Ensure it is **not** a date separator (see below).

**Data Extraction:**
*   **Timestamp:** Directly from `dataset.itemKey`. **Do not** parse the visible time string (e.g., "11:59 AM") as it lacks precision.
*   **User Name:**
    *   Look for the sender button: `button[data-message-sender]` or `.c-message_kit__sender button`.
    *   *Note:* Consecutive messages from the same user ("grouped messages") often omit the header. The scraper must track `lastUser` and apply it to subsequent anonymous messages until a new header appears.
*   **Message Text:**
    *   **Preferred:** `[data-qa="message-text"]` (contains the actual user input).
    *   **Rich Text:** Inside `.c-message__message_blocks--rich_text`.
    *   **Fallback:** `[data-qa="message_content"]`.
    *   *Avoid* `innerText` of the root container, as it includes "Reply", "React", and timestamps.

### 3.3. Identifying Date Separators

Slack inserts explicit separators when the date changes.

*   **Selector:** `div[data-qa="virtual-list-item"]`
*   **Distinguishing Attribute:** `aria-roledescription="separator"` (Note: specifically `aria-roledescription`, not just `role` or `roledescription`).
*   **Backup Check:** The `data-item-key` will *not* be a simple float timestamp.

## 4. Execution Protocol

### Step 1: Initial Snapshot
Take a DOM snapshot to verify you are in the correct context and can see `div[data-qa="virtual-list-item"]` elements.

### Step 2: Extraction Script
Execute a JavaScript function via `evaluate_script` that returns a JSON object containing a list of messages.

**Reference Implementation:**
```javascript
() => {
    const items = document.querySelectorAll('div[data-qa="virtual-list-item"]');
    const messages = [];
    let lastUser = "unknown"; // State for grouped messages

    items.forEach(item => {
        const key = item.dataset.itemKey;
        if (!key) return;

        // 1. Check for Date Separator
        if (item.getAttribute('aria-roledescription') === 'separator') {
            // Logic to track current date context if needed
            return;
        }

        // 2. Check for Message (Float Timestamp)
        if (key.match(/^\d+\.\d+$/)) {
            const ts = key;
            
            // Extract Content
            const textEl = item.querySelector('[data-qa="message-text"]');
            const text = textEl ? textEl.innerText.trim() : "";

            // Extract User
            const senderBtn = item.querySelector('button[data-message-sender], .c-message_kit__sender button');
            if (senderBtn) {
                lastUser = senderBtn.innerText;
            }
            
            // Extract Files
            const files = [];
            item.querySelectorAll('a[href*="files.slack.com"]').forEach(link => {
                files.push({ url: link.href });
            });

            if (text || files.length > 0) {
                messages.push({
                    ts: ts,
                    user: lastUser,
                    text: text,
                    files: files,
                    type: 'message'
                });
            }
        }
    });

    return { messages: messages };
}
```

### Step 3: Pagination (Scrolling)
The virtual list unmounts items as they scroll out of view. You cannot "scroll to bottom and capture all".

**Algorithm:**
1.  Extract currently visible messages.
2.  Store them (deduplicating by `ts`).
3.  Scroll up (simulate `PageUp` key press or use JS `scrollBy`).
4.  Wait for network requests (`conversations.history`) to complete and DOM to update.
5.  Repeat until the "oldest" message timestamp stops changing or a limit is reached.

## 5. Common Pitfalls

1.  **Sidebar Noise:** The sidebar also uses virtualization. If your selector is too broad (e.g., just `role="listitem"`), you might capture channel names instead of messages. **Always** scope to the message pane or use the specific `data-qa` attributes found in the message feed.
2.  **Date Separators vs. Messages:** Always check `aria-roledescription`. A message might contain the text "August 6th" in its body; checking text content alone is insufficient.
3.  **Grouped Messages:** If you don't implement `lastUser` tracking, 50% of messages will have `user: null` because Slack hides the avatar/name for consecutive chats.
