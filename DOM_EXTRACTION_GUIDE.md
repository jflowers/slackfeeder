# DOM Extraction Guide for AI Agents

This guide explains how an AI assistant with MCP browser connectivity (e.g., `chrome-devtools`) should perform Slack message extraction. It details the robust DOM selectors and strategies required to reliably scrape messages and date separators from the Slack web client.

## 1. Overview

The goal is to extract message history from a Slack channel or DM without using the Slack API (which requires an app token). Instead, we automate a browser session to scroll through the history and "scrape" the data directly from the DOM. This includes a specialized process for capturing entire multi-day threads that have recent activity (today or yesterday).

**Prerequisites:**
*   Active browser session logged into Slack.
*   MCP server for Chrome DevTools connected.
*   The conversation to be exported is currently open in the active tab (for main feed).

## 2. Core Strategy

The extraction process involves two main phases:

1.  **Main Conversation History:**
    *   **Selection:** Identifying the correct container elements for messages.
    *   **Extraction:** Parsing timestamps, user names, and message content from those elements.
    *   **Navigation:** Scrolling up to load older messages.
    *   **Deduplication:** Handling the virtual list behavior (where items are reused/unmounted) by tracking unique message IDs.

2.  **Multi-Day Thread History (Supplemental):**
    *   **Discovery:** Navigating to the global "Threads" view to identify threads with recent activity.
    *   **Expansion:** Iteratively clicking "Show N more replies" within thread sidebars to load full thread history.
    *   **Scoped Extraction:** Extracting all messages (root + replies) from the expanded thread sidebar.
    *   **Integration:** Merging these full thread contexts with the main conversation history.

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

**Algorithm: Overlap Verification (Chain of Custody)**
This algorithm ensures a complete, gap-free message history is collected by verifying each newly loaded chunk of messages connects to the previously collected messages.

1.  **Initial Extraction:** Perform an initial extraction of messages visible in the current viewport.
2.  **Establish Frontier:** Identify the `timestamp` of the *oldest* message retrieved in the initial extraction. This `timestamp` becomes the 'frontier' – the point we aim to scroll past.
3.  **Scroll Up:** Simulate `PageUp` key presses to load older messages. Wait for the DOM to update after scrolling.
4.  **Extract Current View:** Extract all messages currently visible in the DOM.
5.  **Verify Overlap (Gap Detection):**
    *   Find the message with the *newest* `timestamp` in the `current view`.
    *   Compare this `newest_timestamp_in_view` with the `frontier_timestamp`.
    *   **If `newest_timestamp_in_view` is significantly older than `frontier_timestamp`:** A gap is detected. This means we scrolled too far up, and some messages between the `frontier` and the `current view` might have been missed.
6.  **Corrective Scrolling (Gap Bridging):**
    *   If a gap is detected, perform small `ArrowDown` scrolls (or equivalent granular scrolling) and re-extract the view.
    *   Repeat this until an overlap is re-established (i.e., `newest_timestamp_in_view` is no longer significantly older than `frontier_timestamp`). This ensures we find the exact connecting point.
7.  **Collect New Messages:** Add all unique messages from the `current view` that are older than the `frontier_timestamp` to the overall collection.
8.  **Update Frontier:** Set the `timestamp` of the *oldest* message in the `current view` as the new `frontier_timestamp`.
9.  **Repeat:** Continue steps 3-8 until the `frontier_timestamp` is older than the `start_date` specified by the user, or no new unique messages are found after multiple scroll attempts (indicating the beginning of the conversation has been reached).

### Step 3: Pagination (Scrolling)
The virtual list unmounts items as they scroll out of view. You cannot "scroll to bottom and capture all".

**Algorithm: Overlap Verification (Chain of Custody)**
This algorithm ensures a complete, gap-free message history is collected by verifying each newly loaded chunk of messages connects to the previously collected messages.

1.  **Initial Extraction:** Perform an initial extraction of messages visible in the current viewport.
2.  **Establish Frontier:** Identify the `timestamp` of the *oldest* message retrieved in the initial extraction. This `timestamp` becomes the 'frontier' – the point we aim to scroll past.
3.  **Scroll Up:** Simulate `PageUp` key presses to load older messages. Wait for the DOM to update after scrolling.
4.  **Extract Current View:** Extract all messages currently visible in the DOM.
5.  **Verify Overlap (Gap Detection):**
    *   Find the message with the *newest* `timestamp` in the `current view`.
    *   Compare this `newest_timestamp_in_view` with the `frontier_timestamp`.
    *   **If `newest_timestamp_in_view` is significantly older than `frontier_timestamp`:** A gap is detected. This means we scrolled too far up, and some messages between the `frontier` and the `current view` might have been missed.
6.  **Corrective Scrolling (Gap Bridging):**
    *   If a gap is detected, perform small `ArrowDown` scrolls (or equivalent granular scrolling) and re-extract the view.
    *   Repeat this until an overlap is re-established (i.e., `newest_timestamp_in_view` is no longer significantly older than `frontier_timestamp`). This ensures we find the exact connecting point.
7.  **Collect New Messages:** Add all unique messages from the `current view` that are older than the `frontier_timestamp` to the overall collection.
8.  **Update Frontier:** Set the `timestamp` of the *oldest* message in the `current view` as the new `frontier_timestamp`.
9.  **Repeat:** Continue steps 3-8 until the `frontier_timestamp` is older than the `start_date` specified by the user, or no new unique messages are found after multiple scroll attempts (indicating the beginning of the conversation has been reached).

### 4.1. Multi-Day Thread Extraction Protocol

To capture complete multi-day threads (root message + all replies) that have activity on the export day (today or yesterday), use the following protocol:

1.  **Navigate to "Threads" View:**
    *   Click the "Threads" sidebar item (e.g., `uid=26_19`).
    *   Wait for the page to load.

2.  **Scan and Filter Thread Summaries:**
    *   Iterate through the visible thread summary cards in the main panel (`div[role="listitem"]`).
    *   For each card, extract:
        *   **Last Reply Timestamp:** Parse from the card's text content (e.g., "Yesterday at 1:44 PM"). This is crucial for filtering.
        *   **Channel/DM Name:** Extract the conversation identifier from the card's text.
        *   **Clickable Element UID:** Identify the `uid` of the button or link within the card that opens the full thread.
    *   **Filter:** Only consider threads where:
        *   The `Last Reply Timestamp` is from the target export day (today or yesterday).
        *   The `Channel/DM Name` matches the target conversation for the export.

3.  **Expand and Extract Full Thread:**
    *   For each filtered thread summary:
        *   **Click Thread Card:** Click the `clickable element UID` to open the thread in the right-hand sidebar (DOM: `div[role="dialog"][aria-label^="Thread"]`).
        *   **Initial Extraction:** Extract all currently visible messages from the opened thread sidebar using the `extract_messages_from_dom` function, scoping it to the sidebar's DOM selector (`THREAD_SIDEPANEL_SELECTOR`).
        *   **Iterative Reply Loading:**
            *   Look for a "Show N more replies" button within the thread sidebar (e.g., `button[data-qa="show_more_replies_button"]` or `button:contains('Show ')`).
            *   **While button exists AND oldest visible message is not older than 'yesterday':**
                *   Click the "Show N more replies" button.
                *   Wait for new replies to load.
                *   Extract new messages from the sidebar and deduplicate with previously collected thread messages.
                *   Re-evaluate the oldest message's timestamp to determine if further loading is needed.
        *   **Close Thread Sidebar:** After all relevant replies are loaded, click the "Close" button (`button[aria-label="Close"]`) within the thread sidebar to return to the main "Threads" view.

4.  **Aggregate and Output:**
    *   Collect all messages from these expanded threads.
    *   Deduplicate messages (if any overlap with the main conversation history).
    *   Output these messages to a separate file (e.g., `[Conversation]_active_threads_[Date].txt`) or append them to the main daily export document with a clear separator.

## 5. Common Pitfalls

1.  **Sidebar Noise:** The sidebar also uses virtualization. If your selector is too broad (e.g., just `role="listitem"`), you might capture channel names instead of messages. **Always** scope to the message pane or use the specific `data-qa` attributes found in the message feed.
2.  **Date Separators vs. Messages:** Always check `aria-roledescription`. A message might contain the text "August 6th" in its body; checking text content alone is insufficient.
3.  **Grouped Messages:** If you don't implement `lastUser` tracking, 50% of messages will have `user: null` because Slack hides the avatar/name for consecutive chats.
