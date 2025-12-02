"""
Browser-based Slack DM scraper using chrome-devtools MCP server.

This module provides functionality to export DMs from Slack by:
1. Controlling a browser session (pre-positioned by user)
2. Capturing network requests (conversations.history API calls)
3. Processing captured responses to extract messages

This approach doesn't require a Slack app/bot token, but requires:
- A browser session with Slack already logged in
- Chrome DevTools Protocol access (via MCP server)
"""

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from src.utils import setup_logging

logger = setup_logging()

# Constants
SCROLL_DELAY_SECONDS = 1.0  # Delay between scroll actions
NETWORK_REQUEST_WAIT_SECONDS = (
    3.0  # Wait time for network requests after scrolling (increased from 2.0)
)
CONVERSATIONS_HISTORY_ENDPOINT = "conversations.history"
MAX_SCROLL_ATTEMPTS = 100  # Maximum number of scroll attempts before stopping


class BrowserScraper:
    """Browser-based scraper for Slack DMs using chrome-devtools MCP."""

    def __init__(self, mcp_client=None):
        """Initialize browser scraper.

        Args:
            mcp_client: Optional MCP client for chrome-devtools. If None, assumes
                       MCP tools are available globally (via function calls).
        """
        self.mcp_client = mcp_client
        self.captured_responses: List[Dict[str, Any]] = []
        self.processed_message_ids: Set[str] = set()

    def _call_mcp_tool(self, tool_name: str, **kwargs) -> Any:
        """Call an MCP tool, either via client or assume it's available globally.

        This is a placeholder - in practice, MCP tools are called directly
        when using the chrome-devtools MCP server.
        """
        if self.mcp_client:
            return getattr(self.mcp_client, tool_name)(**kwargs)
        # If no client, assume tools are available in the calling context
        # This allows the class to work with direct MCP tool calls
        raise NotImplementedError(
            "MCP tools must be called directly when using chrome-devtools MCP server"
        )

    def capture_conversation_history_responses(
        self,
        scroll_attempts: int = MAX_SCROLL_ATTEMPTS,
        output_dir: Optional[Path] = None,
    ) -> List[Dict[str, Any]]:
        """Capture conversations.history API responses by scrolling through Slack.

        This method should be called when a browser is already positioned on a Slack DM.
        It will scroll up to trigger API calls and capture the responses.

        Args:
            scroll_attempts: Maximum number of scroll attempts
            output_dir: Optional directory to save captured responses as JSON files

        Returns:
            List of captured API response dictionaries

        Note:
            This method requires chrome-devtools MCP server to be available.
            The browser should be pre-positioned on the Slack DM conversation.
        """
        logger.info("Starting browser-based DM export")
        logger.warning(
            "This method requires chrome-devtools MCP server and a pre-positioned browser"
        )

        captured_responses = []
        last_oldest_timestamp: Optional[str] = None
        consecutive_no_new_messages = 0

        for attempt in range(scroll_attempts):
            logger.info(f"Scroll attempt {attempt + 1}/{scroll_attempts}")

            # Scroll up to trigger API call
            # Note: In practice, this would use mcp_chrome-devtools_press_key("PageUp")
            # but we can't call it directly here - it should be called by the user
            # or via a wrapper script

            # Wait for network requests
            time.sleep(NETWORK_REQUEST_WAIT_SECONDS)

            # Capture network requests
            # Note: This would use mcp_chrome-devtools_list_network_requests()
            # and mcp_chrome-devtools_get_network_request(reqid)
            # In practice, these are called externally

            # Check if we've reached the beginning
            # If has_more is False in the last response, we're done

        logger.info(f"Captured {len(captured_responses)} API responses")
        return captured_responses

    def save_captured_response(
        self, response_data: Dict[str, Any], output_dir: Path, index: int
    ) -> Path:
        """Save a captured API response to a JSON file.

        Args:
            response_data: The API response dictionary
            output_dir: Directory to save the response
            index: Index number for the filename

        Returns:
            Path to the saved file
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        filename = f"response_{index}.json"
        filepath = output_dir / filename

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(response_data, f, indent=2, ensure_ascii=False)

        logger.info(f"Saved captured response to {filepath}")
        return filepath

    def load_captured_responses(self, response_dir: Path) -> List[Dict[str, Any]]:
        """Load previously captured API responses from JSON files.

        Args:
            response_dir: Directory containing response JSON files

        Returns:
            List of loaded response dictionaries
        """
        responses = []
        if not response_dir.exists():
            logger.warning(f"Response directory does not exist: {response_dir}")
            return responses

        # Sort files numerically by the number in the filename (response_0.json, response_1.json, etc.)
        # This handles cases where there are 10+ files correctly
        def extract_number(path: Path) -> int:
            """Extract number from filename like 'response_42.json' -> 42"""
            try:
                name = path.stem  # 'response_42'
                number_str = name.split("_", 1)[1] if "_" in name else "0"
                return int(number_str)
            except (ValueError, IndexError):
                # Fallback to file modification time if filename parsing fails
                return int(path.stat().st_mtime)

        response_files = sorted(response_dir.glob("response_*.json"), key=extract_number)
        logger.info(f"Loading {len(response_files)} captured response files")

        for response_file in response_files:
            try:
                with open(response_file, "r", encoding="utf-8") as f:
                    response_data = json.load(f)
                    responses.append(response_data)
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"Failed to load {response_file}: {e}")

        return responses


def extract_messages_from_response(response_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extract messages from a conversations.history API response.

    Args:
        response_data: API response dictionary

    Returns:
        List of message dictionaries
    """
    if not isinstance(response_data, dict):
        return []

    if not response_data.get("ok"):
        logger.warning("API response indicates failure")
        return []

    messages = response_data.get("messages", [])
    if not isinstance(messages, list):
        return []

    return messages


def get_response_metadata(response_data: Dict[str, Any]) -> Dict[str, Any]:
    """Extract metadata from an API response.

    Args:
        response_data: API response dictionary

    Returns:
        Dictionary with metadata (has_more, oldest, latest, etc.)
    """
    return {
        "has_more": response_data.get("has_more", False),
        "oldest": response_data.get("oldest"),
        "latest": response_data.get("latest"),
        "message_count": len(response_data.get("messages", [])),
    }


def find_conversations_history_requests(
    network_requests: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Filter network requests to find conversations.history API calls.

    Args:
        network_requests: List of network request dictionaries from chrome-devtools

    Returns:
        List of conversations.history requests
    """
    history_requests = []
    for req in network_requests:
        url = req.get("url", "")
        if CONVERSATIONS_HISTORY_ENDPOINT in url:
            history_requests.append(req)
    return history_requests


def extract_messages_from_dom_script() -> str:
    """Return JavaScript code to extract messages from Slack DOM.

    Accepts an optional containerSelector to scope message extraction to a specific element.

    Returns:
        JavaScript function as string that extracts messages from the page
    """
    return """
    (containerSelector) => {
        const container = containerSelector ? document.querySelector(containerSelector) : document;
        if (!container) {
            return { ok: false, messages: [], message_count: 0, oldest: null, latest: null };
        }

        const items = container.querySelectorAll('div[data-qa="virtual-list-item"]');
        const messages = [];
        let lastUser = "unknown";

        items.forEach(item => {
            const key = item.dataset.itemKey;
            if (!key) return;

            // Skip date separators (marked by non-float key or roledescription)
            if (item.getAttribute('roledescription') === 'separator' || !key.match(/^\\d+\\.\\d+$/)) {
                return;
            }

            // Message Check (Float Timestamp)
            // Timestamp is directly in the key
            const ts = key;

            // Text Content
            // Prefer dedicated message-text element, fallback to message content, then item text
            const textEl = item.querySelector('[data-qa="message-text"]');
            let text = "";
            if (textEl) {
                // Get text from rich text blocks if available
                const richText = textEl.querySelector('.c-message__message_blocks--rich_text');
                text = richText ? richText.innerText : textEl.innerText;
            } else {
                const contentEl = item.querySelector('[data-qa="message_content"]');
                text = contentEl ? contentEl.innerText : item.innerText;
            }
            text = text.trim();

            // User Name
            // Try to find the sender button
            // Note: Consecutive messages often omit the sender button (grouping)
            // We use the last seen user in that case
            // The sender button usually has a specific class or is the first button in the gutter
            let user = null;

            // Strategy 1: Look for button in the left gutter or specific sender container
            // (Slack structure varies, but usually sender is a button in c-message_kit__sender or similar)
            const senderBtn = item.querySelector('button[data-message-sender], .c-message_kit__sender button');
            if (senderBtn) {
                user = senderBtn.innerText;
            } else {
                // Strategy 2: Look for *any* button that isn't an action button
                // (like reactions, reply, etc.) - heuristic approach
                const buttons = item.querySelectorAll('button');
                for (const btn of buttons) {
                    const txt = btn.innerText.trim();
                    // Filter out common UI buttons
                    if (txt && txt.length > 1 &&
                        !['React', 'Reply', 'More actions', 'Add reaction', 'Share'].some(s => txt.includes(s)) &&
                        !txt.match(/^\\d{1,2}:\\d{2}/) && // Time
                        !txt.match(/^\\d+ reply/) // Thread reply count
                    ) {
                        // High probability this is the user name if it appears before the message text
                        // Check if it is "above" the text visually or in DOM order
                        user = txt;
                        break;
                    }
                }
            }

            if (user) {
                lastUser = user;
            } else {
                // Grouped message, use last known user
                user = lastUser;
            }

            // File attachments check
            const files = [];
            const fileLinks = item.querySelectorAll('a[href*="files.slack.com"]');
            fileLinks.forEach(link => {
                const img = link.querySelector('img');
                if (img) {
                    files.push({
                        url: link.href,
                        name: link.getAttribute('download') || link.href.split('/').pop(),
                        thumb_url: img.src
                    });
                }
            });

            // If we have text or files, add the message
            if (text.length > 0 || files.length > 0) {
                messages.push({
                    ts: ts,
                    user: user || 'unknown',
                    text: text,
                    files: files,
                    type: 'message'
                });
            }
        });

        // Sort by timestamp just in case DOM order wasn't perfect (though it usually is)
        messages.sort((a, b) => parseFloat(a.ts) - parseFloat(b.ts));

        return {
            ok: true,
            messages: messages,
            message_count: messages.length,
            oldest: messages.length > 0 ? messages[0].ts : null,
            latest: messages.length > 0 ? messages[messages.length - 1].ts : null
        };
    }
    """

def extract_date_separators_script() -> str:
    """Return JavaScript code to extract date separators from Slack DOM.

    Returns:
        JavaScript function as string that extracts date separators from the page
    """
    return """
    () => {
        const items = document.querySelectorAll('div[data-qa="virtual-list-item"]');
        const dateSeparators = [];
        const seen = new Set();
        
        items.forEach(item => {
            // Strictly check for separator role to avoid sidebar items
            // Note: attribute is aria-roledescription, not roledescription
            const role = item.getAttribute('aria-roledescription');
            
            if (role === 'separator') {
                const text = item.innerText.trim();
                // Extract date part (remove "Press enter..." suffix if present)
                // "Wednesday, August 6th Press enter to select a date to jump to."
                let dateText = text.split('\\n')[0].replace(/Press enter.*/, '').trim();
                
                // If it looks like a date, add it
                if (dateText.length > 5) {
                     if (!seen.has(dateText)) {
                        seen.add(dateText);
                        
                        // Try to infer timestamp from the next message
                        let timestamp = null;
                        let sibling = item.nextElementSibling;
                        let attempts = 0;
                        while(sibling && attempts < 5) {
                            if (sibling.dataset && sibling.dataset.itemKey && sibling.dataset.itemKey.match(/^\\d+\\.\\d+$/)) {
                                timestamp = sibling.dataset.itemKey;
                                break;
                            }
                            sibling = sibling.nextElementSibling;
                            attempts++;
                        }
                        
                        dateSeparators.push({
                            text: dateText,
                            timestamp: timestamp,
                            fullText: text
                        });
                    }
                }
            }
        });
        
        return {
            ok: true,
            separators: dateSeparators,
            separator_count: dateSeparators.length,
            visible_dates: dateSeparators.map(s => s.text)
        };
    }
    """


def extract_date_separators_from_dom(mcp_evaluate_script) -> Dict[str, Any]:
    """Extract date separators from Slack DOM using JavaScript.

    Args:
        mcp_evaluate_script: Function to evaluate JavaScript in the browser
                           (e.g., mcp_chrome-devtools_evaluate_script)

    Returns:
        Dictionary with extracted date separators
    """
    logger.debug("Extracting date separators from DOM...")

    script = extract_date_separators_script()

    try:
        result = mcp_evaluate_script(function=script)

        if not result:
            logger.debug("Date separator extraction returned no result")
            return {"ok": False, "separators": [], "separator_count": 0, "visible_dates": []}

        # Handle different response formats
        if isinstance(result, dict):
            if "separators" in result:
                # Already in correct format
                separator_count = len(result.get("separators", []))
                logger.debug(f"Extracted {separator_count} date separators from DOM")
                return result
            elif "result" in result:
                # Nested result
                return result["result"]

        logger.warning(f"Unexpected date separator extraction result format: {type(result)}")
        return {"ok": False, "separators": [], "separator_count": 0, "visible_dates": []}

    except Exception as e:
        logger.warning(f"Failed to extract date separators from DOM: {e}", exc_info=True)
        return {"ok": False, "separators": [], "separator_count": 0, "visible_dates": []}


def extract_messages_from_dom(mcp_evaluate_script, container_selector: Optional[str] = None) -> Dict[str, Any]:
    """Extract messages from Slack DOM using JavaScript.

    Args:
        mcp_evaluate_script: Function to evaluate JavaScript in the browser
        container_selector: Optional CSS selector string to scope the extraction (e.g., '.thread-sidebar')

    Returns:
        Dictionary in API response format with extracted messages
    """
    logger.info(f"Extracting messages from DOM (selector: {container_selector or 'document'})...")

    script = extract_messages_from_dom_script()

    try:
        # Pass containerSelector as an argument to the JS function
        result = mcp_evaluate_script(function=script, args=[{"containerSelector": container_selector}])

        if not result:
            logger.warning("DOM extraction returned no result")
            return {"ok": False, "messages": [], "message_count": 0}

        # Handle different response formats (MCP often nests the actual result)
        if isinstance(result, dict):
            if "messages" in result:
                # Already in correct format
                message_count = len(result.get("messages", []))
                logger.info(f"Extracted {message_count} messages from DOM")
                return result
            elif "result" in result and "messages" in result["result"]:
                # Nested result
                message_count = len(result["result"].get("messages", []))
                logger.info(f"Extracted {message_count} messages from nested DOM result")
                return result["result"]

        logger.warning(f"Unexpected DOM extraction result format: {type(result)}")
        return {"ok": False, "messages": [], "message_count": 0}

    except Exception as e:
        logger.error(f"Failed to extract messages from DOM: {e}", exc_info=True)
        return {"ok": False, "messages": [], "message_count": 0}
