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
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.browser_scraper import extract_date_separators_script, extract_messages_from_dom_script
from src.utils import setup_logging

logger = setup_logging()

# Scrolling constants
PAGE_DOWN_PRESSES_PER_ATTEMPT = 2  # Number of PageUp presses per scroll attempt (reduced from 5)
SCROLL_WAIT_SECONDS = 3.0  # Wait time after scrolling for messages to load (increased from 2.0)
SCROLL_KEY_DELAY_SECONDS = 0.3  # Delay between individual PageUp key presses
CONSECUTIVE_NO_NEW_MESSAGES_THRESHOLD = 5  # Stop after N consecutive attempts with no new messages
MAX_SCROLL_ATTEMPTS = 100  # Maximum scroll attempts before stopping
CONSECUTIVE_COMPLETE_COVERAGE_THRESHOLD = (
    3  # Stop after N consecutive checks showing complete day coverage
)


def _check_date_separator_coverage(
    mcp_evaluate_script: Callable,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict[str, Any]:
    """Check if date separators indicate complete day coverage for the specified date range.

    Args:
        mcp_evaluate_script: MCP function to evaluate JavaScript
        start_date: Optional start date (YYYY-MM-DD format)
        end_date: Optional end date (YYYY-MM-DD format)

    Returns:
        Dictionary with coverage information:
        - complete: bool indicating if all days have complete coverage
        - missing_days: list of dates that don't have complete coverage
        - visible_separators: list of visible date separator texts
    """
    from datetime import datetime, timedelta

    separator_script = extract_date_separators_script()

    try:
        result = mcp_evaluate_script(function=separator_script)

        if not result or not isinstance(result, dict):
            return {"complete": False, "missing_days": [], "visible_separators": []}

        # Handle nested result format
        if "result" in result:
            separator_data = result["result"]
        elif "separators" in result:
            separator_data = result
        else:
            return {"complete": False, "missing_days": [], "visible_separators": []}

        visible_separators = separator_data.get("separators", [])
        visible_dates = [s.get("text", "") for s in visible_separators]

        # If no date range specified, we can't verify completeness
        if not start_date and not end_date:
            return {
                "complete": True,  # Assume complete if no range specified
                "missing_days": [],
                "visible_separators": visible_dates,
            }

        # Parse date range
        if start_date:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
        else:
            start_dt = None

        if end_date:
            end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        else:
            end_dt = None

        # Generate list of dates to check
        dates_to_check = []
        if start_dt and end_dt:
            current = start_dt
            while current <= end_dt:
                dates_to_check.append(current)
                current += timedelta(days=1)
        elif start_dt:
            # Only start date - check from start_date to today
            current = start_dt
            today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
            while current <= today:
                dates_to_check.append(current)
                current += timedelta(days=1)
        elif end_dt:
            # Only end date - can't determine start, skip checking
            return {"complete": True, "missing_days": [], "visible_separators": visible_dates}

        # Check each date for complete coverage
        # A date has complete coverage if:
        # 1. Its separator is visible, AND
        # 2. The previous date's separator is also visible (or it's the first date)
        missing_days = []

        for i, check_date in enumerate(dates_to_check):
            # Format date to match separator format (e.g., "Friday, June 6th")
            # Try multiple formats (avoid %-d which is not portable)
            day_str = str(check_date.day)  # Remove leading zero manually
            date_formats = [
                check_date.strftime("%A, %B ") + day_str,  # "Friday, June 6"
                check_date.strftime("%A, %B %d"),  # "Friday, June 06"
                check_date.strftime("%B ") + day_str,  # "June 6"
                check_date.strftime("%B %d"),  # "June 06"
            ]

            # Add ordinal suffixes
            day = check_date.day
            if 10 <= day % 100 <= 20:
                suffix = "th"
            else:
                suffix = {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")

            date_formats_with_suffix = []
            for fmt in date_formats:
                date_formats_with_suffix.append(fmt + suffix)
                # Also try without suffix
                date_formats_with_suffix.append(fmt)

            # Check if this date's separator is visible
            date_separator_visible = any(
                any(fmt.lower() in sep_text.lower() for fmt in date_formats_with_suffix)
                for sep_text in visible_dates
            )

            if not date_separator_visible:
                # Date separator not visible - might be a gap (no messages) or incomplete coverage
                # Check if there are messages for this date by looking at timestamps
                # For now, mark as missing if separator not visible
                missing_days.append(check_date.strftime("%Y-%m-%d"))
                continue

            # Check if previous date's separator is visible (for complete coverage)
            if i > 0:
                prev_date = dates_to_check[i - 1]
                prev_day_str = str(prev_date.day)  # Remove leading zero manually
                prev_formats = [
                    prev_date.strftime("%A, %B ") + prev_day_str,
                    prev_date.strftime("%A, %B %d"),
                    prev_date.strftime("%B ") + prev_day_str,
                    prev_date.strftime("%B %d"),
                ]

                prev_day = prev_date.day
                if 10 <= prev_day % 100 <= 20:
                    prev_suffix = "th"
                else:
                    prev_suffix = {1: "st", 2: "nd", 3: "rd"}.get(prev_day % 10, "th")

                prev_formats_with_suffix = []
                for fmt in prev_formats:
                    prev_formats_with_suffix.append(fmt + prev_suffix)
                    prev_formats_with_suffix.append(fmt)

                prev_separator_visible = any(
                    any(fmt.lower() in sep_text.lower() for fmt in prev_formats_with_suffix)
                    for sep_text in visible_dates
                )

                if not prev_separator_visible:
                    # Previous date separator not visible - incomplete coverage for current date
                    missing_days.append(check_date.strftime("%Y-%m-%d"))

        return {
            "complete": len(missing_days) == 0,
            "missing_days": missing_days,
            "visible_separators": visible_dates,
        }

    except Exception as e:
        logger.warning(f"Failed to check date separator coverage: {e}", exc_info=True)
        return {"complete": False, "missing_days": [], "visible_separators": []}


def extract_and_save_dom_messages(
    mcp_evaluate_script: Callable,
    mcp_press_key: Callable,
    output_file: Optional[Path] = None,
    append: bool = False,
    auto_scroll: bool = True,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    output_to_stdout: bool = False,
) -> Dict[str, Any]:
    """Extract messages from DOM with robust gap detection and scrolling.

    Implements a "Chain of Custody" scrolling algorithm:
    1. Start at the bottom (latest messages).
    2. Scroll up.
    3. Verify that the new view overlaps with the previous collected messages.
    4. If a gap is detected (scrolled too far), scroll down until overlap is restored.
    5. Continue until the start date is reached.

    Args:
        mcp_evaluate_script: MCP function to evaluate JavaScript
        mcp_press_key: MCP function to press keys
        output_file: Optional path to save extracted messages
        append: If True, append to existing file
        auto_scroll: If True, automatically scroll through conversation
        start_date: Optional start date filter (YYYY-MM-DD format)
        end_date: Optional end date filter (YYYY-MM-DD format)
        output_to_stdout: If True, output JSON to stdout

    Returns:
        Dictionary with extraction results
    """
    if not callable(mcp_press_key):
        raise ValueError("mcp_press_key must be callable")
    if not callable(mcp_evaluate_script):
        raise ValueError("mcp_evaluate_script must be callable")

    script = extract_messages_from_dom_script()
    
    # Load existing messages if appending
    existing_messages = []
    if append and output_file and output_file.exists():
        try:
            with open(output_file, "r", encoding="utf-8") as f:
                existing_data = json.load(f)
                existing_messages = existing_data.get("messages", [])
                logger.info(f"Loaded {len(existing_messages)} existing messages")
        except Exception as e:
            logger.warning(f"Failed to load existing file: {e}")

    # Dictionary to store unique messages by timestamp
    # Initialize with existing messages
    collected_messages_map = {msg.get("ts"): msg for msg in existing_messages if msg.get("ts")}
    
    # Helper to get collected timestamps (for fast lookup)
    def get_collected_timestamps() -> set:
        return set(collected_messages_map.keys())

    if auto_scroll:
        logger.info("Starting robust scrolling with overlap verification...")
        
        # 1. Initial extraction (bottom of view)
        result = mcp_evaluate_script(function=script)
        initial_messages = []
        
        if result and isinstance(result, dict):
            if "messages" in result:
                initial_messages = result.get("messages", [])
            elif "result" in result:
                initial_messages = result["result"].get("messages", [])
        
        if not initial_messages:
            logger.warning("No messages found in initial view. Ensure browser is on the correct page.")
        else:
            logger.info(f"Initial extraction: found {len(initial_messages)} messages")
            for msg in initial_messages:
                ts = msg.get("ts")
                if ts:
                    collected_messages_map[ts] = msg
        
        # Identify the "frontier" - the oldest message we have collected so far
        # We are scrolling UP (back in time), so we want to extend beyond the oldest message
        sorted_timestamps = sorted([float(ts) for ts in collected_messages_map.keys()])
        frontier_ts = sorted_timestamps[0] if sorted_timestamps else float('inf')
        
        # Determine target timestamp from start_date
        target_ts = 0.0
        if start_date:
            from datetime import datetime
            dt = datetime.strptime(start_date, "%Y-%m-%d")
            target_ts = dt.timestamp()
            logger.info(f"Target start date: {start_date} (TS: {target_ts})")
        
        # Main Scroll Loop
        # Logic:
        # - Scroll Up (PageUp)
        # - Extract View
        # - Check if MAX(View) >= Frontier (Overlap)
        # - If Gap: Scroll Down (ArrowDown) until Overlap
        # - Collect all messages < Frontier
        # - Update Frontier = MIN(View)
        
        consecutive_no_new = 0
        max_attempts = 200  # Safety break
        
        for attempt in range(max_attempts):
            logger.info(f"Scroll step {attempt + 1} (Frontier: {frontier_ts})")
            
            # Check if we reached the target
            if target_ts > 0 and frontier_ts < target_ts:
                logger.info(f"Reached target start date ({frontier_ts} < {target_ts}). Stopping.")
                break
                
            # 1. Scroll Up
            mcp_press_key(key="PageUp")
            time.sleep(1.5) # Wait for load
            
            # 2. Extract
            view_result = mcp_evaluate_script(function=script)
            view_messages = []
            if view_result and isinstance(view_result, dict):
                if "messages" in view_result:
                    view_messages = view_result.get("messages", [])
                elif "result" in view_result:
                    view_messages = view_result["result"].get("messages", [])
            
            if not view_messages:
                logger.warning("Empty view after scroll.")
                consecutive_no_new += 1
                if consecutive_no_new > 5:
                    logger.warning("No messages found for 5 steps. Stopping.")
                    break
                continue

            # 3. Verify Overlap
            # Sort view messages by TS descending (newest first)
            # We need the NEWEST message in the current view to connect to the OLDEST message we already have (Frontier)
            view_messages.sort(key=lambda m: float(m.get("ts", 0)), reverse=True)
            
            newest_in_view_ts = float(view_messages[0].get("ts", 0))
            oldest_in_view_ts = float(view_messages[-1].get("ts", 0))
            
            # Check for overlap: Is newest in view >= frontier?
            # Note: Floating point comparison safety
            if newest_in_view_ts < frontier_ts - 0.001:
                logger.warning(f"GAP DETECTED! Newest visible ({newest_in_view_ts}) is older than frontier ({frontier_ts}).")
                logger.info("Attempting to bridge gap by scrolling down...")
                
                # Backtracking Loop
                gap_bridged = False
                for back_step in range(10): # Max 10 small steps down
                    mcp_press_key(key="ArrowDown")
                    mcp_press_key(key="ArrowDown") # Double press for faster but fine movement
                    time.sleep(1.0)
                    
                    # Check view again
                    fix_result = mcp_evaluate_script(function=script)
                    fix_messages = []
                    if fix_result and isinstance(fix_result, dict):
                         if "messages" in fix_result:
                            fix_messages = fix_result.get("messages", [])
                         elif "result" in fix_result:
                            fix_messages = fix_result["result"].get("messages", [])
                    
                    if not fix_messages:
                        continue
                        
                    fix_max_ts = max([float(m.get("ts", 0)) for m in fix_messages])
                    
                    if fix_max_ts >= frontier_ts - 0.001:
                        logger.info(f"Gap bridged! Found overlap at {fix_max_ts}")
                        view_messages = fix_messages # Update our current view to this valid view
                        gap_bridged = True
                        break
                
                if not gap_bridged:
                    logger.error("Failed to bridge gap after backtracking. Continuing with potential data loss.")
                    # We treat current view as the new reality and move on
            
            # 4. Collect messages
            # Add all messages from the valid view
            new_count = 0
            for msg in view_messages:
                ts = msg.get("ts")
                if ts and ts not in collected_messages_map:
                    collected_messages_map[ts] = msg
                    new_count += 1
            
            if new_count > 0:
                logger.info(f"Collected {new_count} new messages.")
                consecutive_no_new = 0
            else:
                logger.info("No new messages in this view (already collected).")
                consecutive_no_new += 1
            
            # 5. Update Frontier
            # The new frontier is the oldest message in the current valid view
            # (We sort view_messages descending above, so last item is oldest)
            current_view_oldest = float(view_messages[-1].get("ts", 0))
            if current_view_oldest < frontier_ts:
                frontier_ts = current_view_oldest
                logger.info(f"New frontier established at {frontier_ts}")
            
            # Stop if stuck
            if consecutive_no_new >= 10: # Increased threshold for safety
                logger.info("No new messages found for 10 consecutive steps. Assuming top of history.")
                break
    
    else:
        # Non-auto-scroll: just extract current view
        logger.info("Extracting messages from current view (no scroll)...")
        result = mcp_evaluate_script(function=script)
        if result and isinstance(result, dict):
             if "messages" in result:
                msgs = result.get("messages", [])
                for msg in msgs:
                    if msg.get("ts"):
                        collected_messages_map[msg["ts"]] = msg

    # Final Processing
    all_messages = list(collected_messages_map.values())
    
    # Filter by date range if specified
    if start_date or end_date:
        from datetime import datetime
        filtered_messages = []
        for msg in all_messages:
            ts = msg.get("ts")
            if not ts: continue
            try:
                msg_dt = datetime.fromtimestamp(float(ts))
                if start_date:
                    s_dt = datetime.strptime(start_date, "%Y-%m-%d")
                    if msg_dt < s_dt: continue
                if end_date:
                    e_dt = datetime.strptime(end_date, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
                    if msg_dt > e_dt: continue
                filtered_messages.append(msg)
            except Exception:
                filtered_messages.append(msg)
        all_messages = filtered_messages

    # Sort
    all_messages.sort(key=lambda m: float(m.get("ts", 0)))
    
    combined_result = {
        "ok": True,
        "messages": all_messages,
        "message_count": len(all_messages),
        "oldest": all_messages[0].get("ts") if all_messages else None,
        "latest": all_messages[-1].get("ts") if all_messages else None,
        "has_more": False
    }
    
    # Output
    if output_file:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            # Write to temp then rename for atomic write
            temp_file = output_file.with_suffix(output_file.suffix + ".tmp")
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(combined_result, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            temp_file.replace(output_file)
            logger.info(f"Saved {len(all_messages)} messages to {output_file}")
        except Exception as e:
            logger.error(f"Failed to save file: {e}")
            
    if output_to_stdout:
        json.dump(combined_result, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        sys.stdout.flush()

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
    logger.info(
        "Example: extract_and_save_dom_messages(output_file, mcp_evaluate_script, mcp_press_key)"
    )
