#!/usr/bin/env python3
"""
Browser-based DM export script.

This script helps export Slack DMs by:
1. Controlling a browser session (via chrome-devtools MCP)
2. Capturing network requests (conversations.history API calls)
3. Processing captured responses

Usage:
    This script is designed to be called with MCP tools available.
    It can also be used standalone if you manually capture API responses.

Example workflow:
    1. Open Slack DM in browser
    2. Run this script to scroll and capture responses
    3. Process captured responses into export files
"""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.browser_response_processor import BrowserResponseProcessor
from src.browser_scraper import (
    BrowserScraper,
    extract_messages_from_response,
    find_conversations_history_requests,
)
from src.utils import setup_logging

logger = setup_logging()

# Constants
SCROLL_DELAY_SECONDS = 1.0
NETWORK_REQUEST_WAIT_SECONDS = 2.0
MAX_SCROLL_ATTEMPTS = 100


def capture_responses_with_mcp(
    output_dir: Path,
    scroll_attempts: int = MAX_SCROLL_ATTEMPTS,
    use_javascript_scroll: bool = True,
) -> List[Path]:
    """Capture API responses using chrome-devtools MCP tools.

    This function uses MCP tools directly (available in Cursor environment).
    Can use JavaScript-based scrolling (preferred) or keyboard scrolling.

    Args:
        output_dir: Directory to save captured responses
        scroll_attempts: Maximum number of scroll attempts
        use_javascript_scroll: If True, use JavaScript to scroll (no keyboard needed).
                              If False, use PageUp key presses.

    Returns:
        List of paths to saved response files

    Note:
        This requires chrome-devtools MCP server to be configured and a browser
        session to be pre-positioned on the Slack DM conversation.
        The browser should be selected/active in the MCP server.
    """
    try:
        # Import MCP tools - these are available in Cursor environment
        # Note: In practice, these would be called via the MCP server
        from mcp_chrome_devtools import (
            list_network_requests,
            get_network_request,
            press_key,
            evaluate_script,
        )

        mcp_available = True
    except ImportError:
        logger.warning(
            "MCP chrome-devtools tools not directly importable. "
            "This function should be called from a context with MCP tools available."
        )
        mcp_available = False

    if not mcp_available:
        logger.error(
            "MCP tools not available. This script requires chrome-devtools MCP server."
        )
        logger.info(
            "To use automated capture, call this from Cursor with chrome-devtools MCP configured."
        )
        logger.info(
            "Alternatively, manually capture API responses and use --process-only mode."
        )
        return []

    output_dir.mkdir(parents=True, exist_ok=True)
    captured_files = []
    seen_response_ids: set = set()

    logger.info("Starting browser-based DM export with MCP")
    logger.info(f"Will attempt {scroll_attempts} scroll operations")
    logger.info(f"Using {'JavaScript' if use_javascript_scroll else 'keyboard'} scrolling")

    for attempt in range(scroll_attempts):
        logger.info(f"Scroll attempt {attempt + 1}/{scroll_attempts}")

        # Scroll up to trigger API call
        try:
            if use_javascript_scroll:
                # Use JavaScript to scroll programmatically
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
                        // Scroll up by setting scrollTop to 0 (top of container)
                        // Or scroll by a large amount to trigger loading
                        const currentScroll = container.scrollTop;
                        container.scrollTop = Math.max(0, currentScroll - 2000);
                        return {success: true, scrolled: currentScroll - container.scrollTop};
                    }
                    
                    // Fallback: scroll window
                    const beforeScroll = window.pageYOffset || document.documentElement.scrollTop;
                    window.scrollTo(0, Math.max(0, beforeScroll - 2000));
                    const afterScroll = window.pageYOffset || document.documentElement.scrollTop;
                    return {success: true, scrolled: beforeScroll - afterScroll, method: 'window'};
                })();
                """
                scroll_result = evaluate_script(function=scroll_script)
                if scroll_result and scroll_result.get("success"):
                    logger.debug(f"Scrolled {scroll_result.get('scrolled', 0)} pixels")
                else:
                    logger.warning("JavaScript scroll may have failed, trying keyboard fallback")
                    press_key(key="PageUp")
            else:
                # Use keyboard scrolling (fallback)
                press_key(key="PageUp")
        except Exception as e:
            logger.error(f"Failed to scroll: {e}")
            break

        # Wait for network requests
        time.sleep(NETWORK_REQUEST_WAIT_SECONDS)

        # List network requests
        try:
            network_requests_result = list_network_requests()
            # Extract requests list from result
            network_requests = network_requests_result.get("requests", [])
        except Exception as e:
            logger.error(f"Failed to list network requests: {e}")
            continue

        # Find conversations.history requests
        history_requests = find_conversations_history_requests(network_requests)

        # Process each new request
        for req in history_requests:
            req_id = req.get("requestId") or req.get("id")
            if not req_id or req_id in seen_response_ids:
                continue

            seen_response_ids.add(req_id)

            # Get response body
            try:
                response_data = get_network_request(reqid=req_id)
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

                # Save response
                filepath = output_dir / f"response_{len(captured_files)}.json"
                with open(filepath, "w", encoding="utf-8") as f:
                    json.dump(response_json, f, indent=2, ensure_ascii=False)

                captured_files.append(filepath)
                logger.info(
                    f"Captured {len(messages)} messages, saved to {filepath.name}"
                )

                # Check if we've reached the beginning
                if not response_json.get("has_more", False):
                    logger.info("Reached beginning of conversation (has_more=false)")
                    return captured_files

            except Exception as e:
                logger.error(f"Failed to get response for request {req_id}: {e}")
                continue

        # Small delay between scroll attempts
        time.sleep(SCROLL_DELAY_SECONDS)

    logger.info(
        f"Completed {scroll_attempts} scroll attempts, captured {len(captured_files)} responses"
    )
    return captured_files


def process_captured_responses(
    response_dir: Path,
    output_dir: Path,
    conversation_name: str = "DM",
    user_map: Optional[Dict[str, str]] = None,
) -> None:
    """Process captured API responses and generate export files.

    Args:
        response_dir: Directory containing captured response JSON files
        output_dir: Directory to write export files
        conversation_name: Name of the conversation (for filename)
        user_map: Optional mapping of user IDs to display names
    """
    processor = BrowserResponseProcessor(user_map=user_map)

    # Find all response files
    response_files = sorted(response_dir.glob("response_*.json"))
    if not response_files:
        logger.error(f"No response files found in {response_dir}")
        return

    logger.info(f"Processing {len(response_files)} response files")

    # Process responses
    total_messages, date_counts = processor.process_responses(
        response_files, output_dir, conversation_name
    )

    logger.info(f"Export complete: {total_messages} messages across {len(date_counts)} dates")


def main():
    """Main entry point for browser export script."""
    parser = argparse.ArgumentParser(
        description="Export Slack DMs using browser-based scraping"
    )
    parser.add_argument(
        "--capture-only",
        action="store_true",
        help="Only capture API responses (don't process)",
    )
    parser.add_argument(
        "--process-only",
        action="store_true",
        help="Only process previously captured responses",
    )
    parser.add_argument(
        "--response-dir",
        type=Path,
        default=Path("browser_exports/api_responses"),
        help="Directory containing captured API responses",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("slack_exports"),
        help="Directory to write export files",
    )
    parser.add_argument(
        "--conversation-name",
        type=str,
        default="DM",
        help="Name of the conversation (for filename)",
    )
    parser.add_argument(
        "--scroll-attempts",
        type=int,
        default=MAX_SCROLL_ATTEMPTS,
        help="Maximum number of scroll attempts",
    )
    parser.add_argument(
        "--use-keyboard-scroll",
        action="store_true",
        help="Use keyboard scrolling (PageUp) instead of JavaScript scrolling",
    )
    parser.add_argument(
        "--user-map",
        type=str,
        help="JSON file with user ID to name mappings",
    )

    args = parser.parse_args()

    # Load user map if provided
    user_map = None
    if args.user_map:
        user_map_path = Path(args.user_map)
        if user_map_path.exists():
            with open(user_map_path, "r", encoding="utf-8") as f:
                user_map = json.load(f)
        else:
            logger.warning(f"User map file not found: {user_map_path}")

    if args.process_only:
        # Only process existing responses
        process_captured_responses(
            args.response_dir, args.output_dir, args.conversation_name, user_map
        )
    elif args.capture_only:
        # Only capture responses (requires MCP tools)
        logger.info("Capture-only mode - attempting to use chrome-devtools MCP...")
        captured_files = capture_responses_with_mcp(
            args.response_dir, args.scroll_attempts, use_javascript_scroll=not args.use_keyboard_scroll
        )
        logger.info(f"Captured {len(captured_files)} response files")
    else:
        # Both capture and process
        logger.info("Full capture+process mode - attempting to capture responses...")
        captured_files = capture_responses_with_mcp(
            args.response_dir, args.scroll_attempts, use_javascript_scroll=not args.use_keyboard_scroll
        )
        if captured_files:
            process_captured_responses(
                args.response_dir, args.output_dir, args.conversation_name, user_map
            )
        else:
            logger.info("No responses captured. Use --process-only to process existing responses.")


if __name__ == "__main__":
    main()
