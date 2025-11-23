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
NETWORK_REQUEST_WAIT_SECONDS = 3.0  # Wait time for network requests after scrolling (increased from 2.0)
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

    def save_captured_response(self, response_data: Dict[str, Any], output_dir: Path, index: int) -> Path:
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
                number_str = name.split('_', 1)[1] if '_' in name else '0'
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

    Returns:
        JavaScript function as string that extracts messages from the page
    """
    return """
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
                    !txt.match(/^\\d{1,2}:\\d{2}/)) {
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
            
            const timePatterns = [/^\\d{1,2}:\\d{2}\\s+AM/, /^\\d{1,2}:\\d{2}\\s+PM/, /^\\d{1,2}:\\d{2}/];
            for (const pattern of timePatterns) {
                const match = text.match(pattern);
                if (match) {
                    text = text.substring(match[0].length).trim();
                    break;
                }
            }
            
            text = text.replace(/React.*/g, '').replace(/Reply.*/g, '').replace(/More.*/g, '');
            text = text.replace(/Add reaction.*/g, '').replace(/\\s+/g, ' ').trim();
            
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
    """


def extract_messages_from_dom(mcp_evaluate_script) -> Dict[str, Any]:
    """Extract messages from Slack DOM using JavaScript.

    Args:
        mcp_evaluate_script: Function to evaluate JavaScript in the browser
                           (e.g., mcp_chrome-devtools_evaluate_script)

    Returns:
        Dictionary in API response format with extracted messages
    """
    logger.info("Extracting messages from DOM...")
    
    script = extract_messages_from_dom_script()
    
    try:
        result = mcp_evaluate_script(function=script)
        
        if not result:
            logger.warning("DOM extraction returned no result")
            return {"ok": False, "messages": [], "message_count": 0}
        
        # Handle different response formats
        if isinstance(result, dict):
            if "messages" in result:
                # Already in correct format
                message_count = len(result.get("messages", []))
                logger.info(f"Extracted {message_count} messages from DOM")
                return result
            elif "result" in result:
                # Nested result
                return result["result"]
        
        logger.warning(f"Unexpected DOM extraction result format: {type(result)}")
        return {"ok": False, "messages": [], "message_count": 0}
        
    except Exception as e:
        logger.error(f"Failed to extract messages from DOM: {e}", exc_info=True)
        return {"ok": False, "messages": [], "message_count": 0}
