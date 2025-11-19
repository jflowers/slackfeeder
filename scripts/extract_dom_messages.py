#!/usr/bin/env python3
"""
Extract messages from Slack DOM using chrome-devtools MCP.

This script extracts messages directly from the DOM of a Slack conversation
that's currently visible in the browser. It can be called interactively
with MCP tools available.

Usage:
    This script is designed to be called from Cursor with MCP chrome-devtools tools.
    It will extract messages from the currently visible page and save them.
    
    With automated scrolling:
    - Automatically scrolls through the conversation
    - Extracts messages after each scroll
    - Combines and deduplicates all messages
    - Stops when no new messages are found
"""

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.browser_scraper import extract_messages_from_dom_script
from src.utils import setup_logging

logger = setup_logging()

# Scrolling constants
PAGE_DOWN_PRESSES_PER_ATTEMPT = 5  # Number of PageDown presses per scroll attempt
SCROLL_WAIT_SECONDS = 2.0  # Wait time after scrolling for messages to load
CONSECUTIVE_NO_NEW_MESSAGES_THRESHOLD = 5  # Stop after N consecutive attempts with no new messages
MAX_SCROLL_ATTEMPTS = 100  # Maximum scroll attempts before stopping


def extract_and_save_dom_messages(
    output_file: Path,
    mcp_evaluate_script,
    mcp_press_key,
    append: bool = False,
    auto_scroll: bool = True,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Extract messages from DOM and save to file.

    Automatically scrolls through the conversation to load all messages by default.
    Uses MCP tools to press PageDown keys and extract messages as they become visible.

    Args:
        output_file: Path to save extracted messages
        mcp_evaluate_script: MCP function to evaluate JavaScript
        mcp_press_key: MCP function to press keys (required for scrolling)
        append: If True, append to existing file; if False, overwrite
        auto_scroll: If True, automatically scroll through conversation (default: True)
        start_date: Optional start date filter (YYYY-MM-DD format)
        end_date: Optional end date filter (YYYY-MM-DD format)

    Returns:
        Dictionary with extraction results
    """
    if not mcp_press_key:
        raise ValueError("mcp_press_key is required for automated scrolling")
    
    script = extract_messages_from_dom_script()
    
    # Load existing messages if appending
    existing_messages = []
    if append and output_file.exists():
        try:
            with open(output_file, "r", encoding="utf-8") as f:
                existing_data = json.load(f)
                existing_messages = existing_data.get("messages", [])
                logger.info(f"Loaded {len(existing_messages)} existing messages")
        except Exception as e:
            logger.warning(f"Failed to load existing file: {e}")
    
    all_extracted_messages = []
    
    if auto_scroll:
        logger.info("Starting automated scrolling and extraction...")
        consecutive_no_new = 0
        
        for attempt in range(MAX_SCROLL_ATTEMPTS):
            logger.info(f"Scroll attempt {attempt + 1}/{MAX_SCROLL_ATTEMPTS}")
            
            # Press PageDown multiple times to load more messages
            for _ in range(PAGE_DOWN_PRESSES_PER_ATTEMPT):
                mcp_press_key(key="PageDown")
            
            # Wait for messages to load
            time.sleep(SCROLL_WAIT_SECONDS)
            
            # Extract messages from current view
            try:
                result = mcp_evaluate_script(function=script)
                
                if result and isinstance(result, dict):
                    if "messages" in result:
                        extracted_data = result
                    elif "result" in result:
                        extracted_data = result["result"]
                    else:
                        logger.warning(f"Unexpected result format: {result.keys()}")
                        continue
                    
                    new_messages = extracted_data.get("messages", [])
                    if new_messages:
                        # Check if we have any truly new messages
                        existing_ts = {msg.get("ts") for msg in all_extracted_messages + existing_messages}
                        new_count = sum(1 for msg in new_messages if msg.get("ts") not in existing_ts)
                        
                        if new_count > 0:
                            logger.info(f"Found {new_count} new messages (total visible: {len(new_messages)})")
                            all_extracted_messages.extend(new_messages)
                            consecutive_no_new = 0
                        else:
                            consecutive_no_new += 1
                            logger.info(f"No new messages found ({consecutive_no_new}/{CONSECUTIVE_NO_NEW_MESSAGES_THRESHOLD})")
                    else:
                        consecutive_no_new += 1
                        logger.info(f"No messages extracted ({consecutive_no_new}/{CONSECUTIVE_NO_NEW_MESSAGES_THRESHOLD})")
                    
                    # Check date range if specified
                    if end_date and extracted_data.get("latest"):
                        from datetime import datetime
                        try:
                            latest_ts = float(extracted_data["latest"])
                            end_dt = datetime.strptime(end_date, "%Y-%m-%d")
                            end_ts = end_dt.timestamp()
                            if latest_ts > end_ts:
                                logger.info(f"Reached end date {end_date}, stopping scroll")
                                break
                        except Exception as e:
                            logger.warning(f"Failed to check end date: {e}")
                    
                    # Stop if no new messages for several attempts
                    if consecutive_no_new >= CONSECUTIVE_NO_NEW_MESSAGES_THRESHOLD:
                        logger.info("No new messages found after multiple attempts, stopping scroll")
                        break
                else:
                    consecutive_no_new += 1
                    
            except Exception as e:
                logger.warning(f"Error during scroll attempt {attempt + 1}: {e}")
                consecutive_no_new += 1
                if consecutive_no_new >= CONSECUTIVE_NO_NEW_MESSAGES_THRESHOLD:
                    break
        
        logger.info(f"Completed scrolling. Extracted {len(all_extracted_messages)} messages total")
        
        # Extract from final view as well
        try:
            result = mcp_evaluate_script(function=script)
            if result and isinstance(result, dict):
                if "messages" in result:
                    final_messages = result.get("messages", [])
                elif "result" in result:
                    final_messages = result["result"].get("messages", [])
                else:
                    final_messages = []
                
                existing_ts = {msg.get("ts") for msg in all_extracted_messages + existing_messages}
                all_extracted_messages.extend(
                    msg for msg in final_messages if msg.get("ts") not in existing_ts
                )
        except Exception as e:
            logger.warning(f"Failed to extract final view: {e}")
    
    # If not auto-scrolling, just extract current view
    if not auto_scroll:
        logger.info("Extracting messages from DOM (current view only)...")
        try:
            result = mcp_evaluate_script(function=script)
            
            if not result:
                logger.warning("DOM extraction returned no result")
                return {"ok": False, "messages": [], "message_count": 0}
            
            # Handle different response formats
            if isinstance(result, dict):
                if "messages" in result:
                    extracted_data = result
                elif "result" in result:
                    extracted_data = result["result"]
                else:
                    logger.warning(f"Unexpected result format: {result.keys()}")
                    return {"ok": False, "messages": [], "message_count": 0}
            else:
                logger.warning(f"Unexpected result type: {type(result)}")
                return {"ok": False, "messages": [], "message_count": 0}
            
            all_extracted_messages = extracted_data.get("messages", [])
            
        except Exception as e:
            logger.error(f"Failed to extract messages from DOM: {e}", exc_info=True)
            return {"ok": False, "messages": [], "message_count": 0}
    
    # Combine with existing messages
    all_messages = existing_messages + all_extracted_messages
    
    # Filter by date range if specified
    if start_date or end_date:
        from datetime import datetime
        filtered_messages = []
        for msg in all_messages:
            ts = msg.get("ts")
            if not ts:
                continue
            try:
                msg_dt = datetime.fromtimestamp(float(ts))
                if start_date:
                    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
                    if msg_dt < start_dt:
                        continue
                if end_date:
                    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
                    # Include entire end date (up to end of day)
                    end_dt = end_dt.replace(hour=23, minute=59, second=59)
                    if msg_dt > end_dt:
                        continue
                filtered_messages.append(msg)
            except Exception as e:
                logger.warning(f"Failed to parse timestamp {ts}: {e}")
                filtered_messages.append(msg)  # Include if we can't parse
        all_messages = filtered_messages
    
    # Deduplicate by timestamp
    seen_ts = set()
    unique_messages = []
    for msg in all_messages:
        ts = msg.get("ts")
        if ts and ts not in seen_ts:
            seen_ts.add(ts)
            unique_messages.append(msg)
    
    # Sort by timestamp
    unique_messages.sort(key=lambda m: float(m.get("ts", 0)))
    
    # Create combined result
    combined_result = {
        "ok": True,
        "messages": unique_messages,
        "message_count": len(unique_messages),
        "oldest": unique_messages[0].get("ts") if unique_messages else None,
        "latest": unique_messages[-1].get("ts") if unique_messages else None,
        "has_more": False,
    }
    
    # Save to file
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(combined_result, f, indent=2, ensure_ascii=False)
    
    logger.info(
        f"Saved {len(unique_messages)} unique messages to {output_file}"
    )
    logger.info(
        f"Date range: {combined_result['oldest']} to {combined_result['latest']}"
    )
    
    return combined_result


if __name__ == "__main__":
    # This script is designed to be called interactively with MCP tools
    # Example usage from Cursor:
    # from scripts.extract_dom_messages import extract_and_save_dom_messages
    # 
    # # Automated scrolling (default):
    # result = extract_and_save_dom_messages(
    #     Path("browser_exports/response_dom_extraction.json"),
    #     mcp_chrome-devtools_evaluate_script,
    #     mcp_chrome-devtools_press_key,
    #     start_date="2025-11-01",
    #     end_date="2025-11-18"
    # )
    #
    # # Without auto-scrolling (manual scroll first, then extract):
    # result = extract_and_save_dom_messages(
    #     Path("browser_exports/response_dom_extraction.json"),
    #     mcp_chrome-devtools_evaluate_script,
    #     mcp_chrome-devtools_press_key,
    #     auto_scroll=False
    # )
    logger.info("This script should be imported and called with MCP tools")
    logger.info("Example: extract_and_save_dom_messages(output_file, mcp_evaluate_script, mcp_press_key)")
