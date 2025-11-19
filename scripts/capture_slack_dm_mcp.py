#!/usr/bin/env python3
"""
Helper script to capture Slack DM API responses using chrome-devtools MCP tools.

This script is designed to be called from Cursor where MCP tools are available.
It provides a simple interface to capture API responses by scrolling through Slack.

Usage in Cursor:
    You can call the MCP tools directly or use this script as a reference.
    The actual capture happens via chrome-devtools MCP server.
"""

import json
import sys
import time
from pathlib import Path
from typing import List, Optional

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.browser_scraper import (
    extract_messages_from_response,
    find_conversations_history_requests,
)
from src.utils import setup_logging

logger = setup_logging()

# Constants
SCROLL_DELAY_SECONDS = 0.3  # Delay between individual Page Down presses
NETWORK_REQUEST_WAIT_SECONDS = 4.0  # Wait time for network requests after scrolling
MAX_SCROLL_ATTEMPTS = 200  # Maximum number of scroll attempts before stopping
PAGE_DOWN_PRESSES_PER_ATTEMPT = 5  # Number of Page Down presses per scroll attempt
CONSECUTIVE_NO_NEW_MESSAGES_THRESHOLD = 5  # Stop after this many attempts with no new messages


def capture_with_mcp_tools(
    output_dir: Path,
    scroll_attempts: int = MAX_SCROLL_ATTEMPTS,
    mcp_list_network_requests=None,
    mcp_get_network_request=None,
    mcp_press_key=None,
    mcp_evaluate_script=None,
    use_keyboard_scroll: bool = True,
    end_date_timestamp: Optional[float] = None,
) -> List[Path]:
    """Capture API responses using MCP tools (callable from Cursor).

    This function accepts MCP tool functions as parameters so it can be called
    from Cursor with the actual MCP tools.

    Args:
        output_dir: Directory to save captured responses
        scroll_attempts: Maximum number of scroll attempts
        mcp_list_network_requests: Function to list network requests
        mcp_get_network_request: Function to get a network request by ID
        mcp_press_key: Function to press a key (for keyboard scrolling)
        mcp_evaluate_script: Function to evaluate JavaScript (for programmatic scrolling)
        use_keyboard_scroll: If True, use Page Down keys (preferred). If False, use JavaScript scrolling.
        end_date_timestamp: Optional Unix timestamp to stop scrolling when reached (for date range filtering)

    Returns:
        List of paths to saved response files
    """
    if not all([mcp_list_network_requests, mcp_get_network_request]):
        logger.error("MCP tools (list_network_requests, get_network_request) must be provided")
        return []
    
    if use_keyboard_scroll and not mcp_press_key:
        logger.warning("Keyboard scrolling requested but press_key not provided, falling back to JavaScript")
        use_keyboard_scroll = False
    
    if not use_keyboard_scroll and not mcp_evaluate_script:
        logger.error("JavaScript scrolling requested but evaluate_script not provided")
        return []

    output_dir.mkdir(parents=True, exist_ok=True)
    captured_files = []
    seen_response_ids = set()
    consecutive_no_new_messages = 0
    latest_message_timestamp: Optional[float] = None

    logger.info("Starting browser-based DM export with MCP")
    logger.info(f"Will attempt up to {scroll_attempts} scroll operations")
    logger.info(f"Using {'Page Down keys' if use_keyboard_scroll else 'JavaScript'} scrolling")
    logger.info(f"Pressing Page Down {PAGE_DOWN_PRESSES_PER_ATTEMPT} times per attempt")
    if end_date_timestamp:
        logger.info(f"Will stop when messages exceed timestamp: {end_date_timestamp}")

    for attempt in range(scroll_attempts):
        logger.info(f"Scroll attempt {attempt + 1}/{scroll_attempts}")

        # Scroll down to trigger API call (scroll down to load older messages)
        try:
            if use_keyboard_scroll and mcp_press_key:
                # Press Page Down multiple times per attempt for more aggressive scrolling
                for i in range(PAGE_DOWN_PRESSES_PER_ATTEMPT):
                    mcp_press_key(key="PageDown")
                    time.sleep(SCROLL_DELAY_SECONDS)  # Small delay between key presses
                logger.debug(f"Pressed Page Down {PAGE_DOWN_PRESSES_PER_ATTEMPT} times")
            elif mcp_evaluate_script:
                # Use JavaScript scrolling as fallback
                scroll_script = """
                (function() {
                    // Find Slack's message container - try common selectors
                    const containers = [
                        document.querySelector('[data-qa="slack_kit_scrollbar"]'),
                        document.querySelector('.c-message_list'),
                        document.querySelector('[role="log"]'),
                        document.querySelector('.p-message_pane'),
                        document.querySelector('[data-qa="virtualized_list"]'),
                    ].filter(Boolean);
                    
                    if (containers.length > 0) {
                        const container = containers[0];
                        // Scroll down by a large amount to trigger loading
                        const currentScroll = container.scrollTop;
                        container.scrollTop = currentScroll + 2000;
                        return {success: true, scrolled: container.scrollTop - currentScroll, method: 'container'};
                    }
                    
                    // Fallback: scroll window
                    const beforeScroll = window.pageYOffset || document.documentElement.scrollTop;
                    window.scrollTo(0, beforeScroll + 2000);
                    const afterScroll = window.pageYOffset || document.documentElement.scrollTop;
                    return {success: true, scrolled: afterScroll - beforeScroll, method: 'window'};
                })();
                """
                scroll_result = mcp_evaluate_script(function=scroll_script)
                if scroll_result and scroll_result.get("success"):
                    logger.debug(f"Scrolled {scroll_result.get('scrolled', 0)} pixels using {scroll_result.get('method', 'unknown')}")
            else:
                logger.error("No scrolling method available")
                break
        except Exception as e:
            logger.error(f"Failed to scroll: {e}")
            break

        # Wait for network requests to complete
        time.sleep(NETWORK_REQUEST_WAIT_SECONDS)

        # List network requests
        try:
            network_requests_result = mcp_list_network_requests(
                resourceTypes=["xhr", "fetch"]
            )
            # Handle different response formats
            if isinstance(network_requests_result, dict):
                network_requests = network_requests_result.get("requests", [])
            elif isinstance(network_requests_result, list):
                network_requests = network_requests_result
            else:
                logger.warning(f"Unexpected network requests format: {type(network_requests_result)}")
                consecutive_no_new_messages += 1
                if consecutive_no_new_messages >= CONSECUTIVE_NO_NEW_MESSAGES_THRESHOLD:
                    logger.info(f"No new messages for {consecutive_no_new_messages} attempts, stopping")
                    break
                continue
        except Exception as e:
            logger.error(f"Failed to list network requests: {e}")
            consecutive_no_new_messages += 1
            if consecutive_no_new_messages >= CONSECUTIVE_NO_NEW_MESSAGES_THRESHOLD:
                logger.info(f"No new messages for {consecutive_no_new_messages} attempts, stopping")
                break
            continue

        # Find conversations.history requests
        history_requests = find_conversations_history_requests(network_requests)

        # Track if we captured any new messages in this attempt
        new_messages_captured = False

        # Process each new request
        for req in history_requests:
            req_id = req.get("requestId") or req.get("id")
            if not req_id or req_id in seen_response_ids:
                continue

            seen_response_ids.add(req_id)

            # Get response body
            try:
                response_data = mcp_get_network_request(reqid=req_id)
                # Extract response body - structure may vary
                response_body = ""
                if isinstance(response_data, dict):
                    response_body = (
                        response_data.get("response", {})
                        .get("body", "")
                        or response_data.get("body", "")
                    )

                if not response_body:
                    continue

                # Parse response
                try:
                    if isinstance(response_body, str):
                        response_json = json.loads(response_body)
                    else:
                        response_json = response_body
                except (json.JSONDecodeError, TypeError):
                    logger.warning(f"Failed to parse response for request {req_id}")
                    continue

                # Extract messages to check if this is a valid response
                messages = extract_messages_from_response(response_json)
                if not messages:
                    logger.debug(f"No messages in response {req_id}")
                    continue

                # Check if we've exceeded the end date timestamp
                if end_date_timestamp:
                    # Get the oldest message timestamp (most recent message in the response)
                    oldest_ts = response_json.get("oldest")
                    if oldest_ts:
                        try:
                            oldest_float = float(oldest_ts)
                            if oldest_float > end_date_timestamp:
                                logger.info(
                                    f"Reached end date limit: oldest message timestamp {oldest_ts} "
                                    f"exceeds end date {end_date_timestamp}"
                                )
                                return captured_files
                        except (ValueError, TypeError):
                            pass

                # Update latest message timestamp tracking
                latest_ts = response_json.get("latest")
                if latest_ts:
                    try:
                        latest_float = float(latest_ts)
                        if latest_message_timestamp is None or latest_float > latest_message_timestamp:
                            latest_message_timestamp = latest_float
                    except (ValueError, TypeError):
                        pass

                # Save response
                filepath = output_dir / f"response_{len(captured_files)}.json"
                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(response_json, f, indent=2, ensure_ascii=False)

                captured_files.append(filepath)
                new_messages_captured = True
                consecutive_no_new_messages = 0  # Reset counter
                
                logger.info(
                    f"Captured {len(messages)} messages (oldest: {response_json.get('oldest')}, "
                    f"latest: {response_json.get('latest')}), saved to {filepath.name}"
                )

                # Check if we've reached the beginning
                if not response_json.get("has_more", False):
                    logger.info("Reached beginning of conversation (has_more=false)")
                    return captured_files

            except Exception as e:
                logger.error(f"Failed to get response for request {req_id}: {e}")
                continue

        # If no new messages were captured, increment counter
        if not new_messages_captured:
            consecutive_no_new_messages += 1
            logger.debug(f"No new messages captured (consecutive: {consecutive_no_new_messages})")
            if consecutive_no_new_messages >= CONSECUTIVE_NO_NEW_MESSAGES_THRESHOLD:
                logger.info(
                    f"No new messages for {consecutive_no_new_messages} consecutive attempts, stopping"
                )
                break
        else:
            consecutive_no_new_messages = 0  # Reset counter if we captured messages

        # Exponential backoff: increase wait time slightly as we progress
        # This helps ensure we don't miss messages when scrolling through large conversations
        backoff_delay = SCROLL_DELAY_SECONDS * (1 + (attempt * 0.01))
        time.sleep(backoff_delay)

    logger.info(
        f"Completed {scroll_attempts} scroll attempts, captured {len(captured_files)} responses"
    )
    return captured_files


if __name__ == "__main__":
    # This script is meant to be called from Cursor with MCP tools
    # In practice, you would call capture_with_mcp_tools() with the actual MCP functions
    logger.info("This script provides a helper function for MCP tool integration.")
    logger.info("Call capture_with_mcp_tools() with MCP tool functions from Cursor.")
