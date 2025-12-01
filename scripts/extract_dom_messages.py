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
    """Extract messages from DOM and optionally save to file or output to stdout.

    Automatically scrolls through the conversation to load all messages by default.
    Uses MCP tools to press PageDown keys and extract messages as they become visible.

    Args:
        mcp_evaluate_script: MCP function to evaluate JavaScript (must be callable)
        mcp_press_key: MCP function to press keys (required for scrolling, must be callable)
        output_file: Optional path to save extracted messages (if None and output_to_stdout=False, no file is created)
        append: If True, append to existing file; if False, overwrite
        auto_scroll: If True, automatically scroll through conversation (default: True)
        start_date: Optional start date filter (YYYY-MM-DD format)
        end_date: Optional end date filter (YYYY-MM-DD format)
        output_to_stdout: If True, output JSON to stdout instead of (or in addition to) file

    Returns:
        Dictionary with extraction results

    Raises:
        ValueError: If mcp_press_key or mcp_evaluate_script are not callable
        OSError: If file cannot be written (only if output_file is provided)
        PermissionError: If file permissions prevent writing (only if output_file is provided)
    """
    if not callable(mcp_press_key):
        raise ValueError("mcp_press_key must be callable")
    if not callable(mcp_evaluate_script):
        raise ValueError("mcp_evaluate_script must be callable")

    script = extract_messages_from_dom_script()

    # Load existing messages if appending and output_file is provided
    existing_messages = []
    if append and output_file and output_file.exists():
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
        consecutive_complete_coverage = 0

        for attempt in range(MAX_SCROLL_ATTEMPTS):
            logger.info(f"Scroll attempt {attempt + 1}/{MAX_SCROLL_ATTEMPTS}")

            # CRITICAL: Extract messages BEFORE scrolling to capture messages that might
            # disappear from DOM when scrolling (Slack uses virtual scrolling)
            try:
                pre_scroll_result = mcp_evaluate_script(function=script)
                if pre_scroll_result and isinstance(pre_scroll_result, dict):
                    if "messages" in pre_scroll_result:
                        pre_scroll_messages = pre_scroll_result.get("messages", [])
                    elif "result" in pre_scroll_result:
                        pre_scroll_messages = pre_scroll_result["result"].get("messages", [])
                    else:
                        pre_scroll_messages = []

                    # Add any new messages from pre-scroll extraction
                    existing_ts = {msg.get("ts") for msg in all_extracted_messages} | {
                        msg.get("ts") for msg in existing_messages
                    }
                    new_pre_count = sum(
                        1 for msg in pre_scroll_messages if msg.get("ts") not in existing_ts
                    )
                    if new_pre_count > 0:
                        logger.info(
                            f"Found {new_pre_count} new messages before scrolling (total visible: {len(pre_scroll_messages)})"
                        )
                        all_extracted_messages.extend(pre_scroll_messages)
                else:
                    pre_scroll_messages = []
            except Exception as e:
                logger.warning(f"Failed to extract messages before scroll: {e}")
                pre_scroll_messages = []

            # Press PageUp multiple times to load older messages (scroll backward)
            # Add small delay between each press to avoid being too aggressive
            for _ in range(PAGE_DOWN_PRESSES_PER_ATTEMPT):
                mcp_press_key(key="PageUp")
                time.sleep(SCROLL_KEY_DELAY_SECONDS)

            # Wait for messages to load
            time.sleep(SCROLL_WAIT_SECONDS)

            # Extract messages from current view AFTER scrolling
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
                        # Optimize: avoid list concatenation by using set union
                        existing_ts = {msg.get("ts") for msg in all_extracted_messages} | {
                            msg.get("ts") for msg in existing_messages
                        }
                        new_count = sum(
                            1 for msg in new_messages if msg.get("ts") not in existing_ts
                        )

                        if new_count > 0:
                            logger.info(
                                f"Found {new_count} new messages (total visible: {len(new_messages)})"
                            )
                            all_extracted_messages.extend(new_messages)
                            consecutive_no_new = 0
                        else:
                            consecutive_no_new += 1
                            logger.info(
                                f"No new messages found ({consecutive_no_new}/{CONSECUTIVE_NO_NEW_MESSAGES_THRESHOLD})"
                            )
                    else:
                        consecutive_no_new += 1
                        logger.info(
                            f"No messages extracted ({consecutive_no_new}/{CONSECUTIVE_NO_NEW_MESSAGES_THRESHOLD})"
                        )

                    # Check date separator coverage to ensure complete day coverage
                    if start_date or end_date:
                        coverage_info = _check_date_separator_coverage(
                            mcp_evaluate_script, start_date=start_date, end_date=end_date
                        )

                        if coverage_info.get("complete"):
                            consecutive_complete_coverage += 1
                            logger.info(
                                f"Complete day coverage confirmed ({consecutive_complete_coverage}/"
                                f"{CONSECUTIVE_COMPLETE_COVERAGE_THRESHOLD}). "
                                f"Visible dates: {', '.join(coverage_info.get('visible_separators', [])[:5])}"
                            )

                            # If we have complete coverage for multiple consecutive checks, we're done
                            if (
                                consecutive_complete_coverage
                                >= CONSECUTIVE_COMPLETE_COVERAGE_THRESHOLD
                            ):
                                logger.info(
                                    "Complete day coverage confirmed for multiple consecutive checks, "
                                    "stopping scroll"
                                )
                                break
                        else:
                            consecutive_complete_coverage = 0
                            missing_days = coverage_info.get("missing_days", [])
                            if missing_days:
                                logger.info(
                                    f"Incomplete day coverage detected. Missing days: "
                                    f"{', '.join(missing_days[:5])}"
                                    f"{'...' if len(missing_days) > 5 else ''}"
                                )

                    # Check date range if specified (for backward scrolling, check oldest/start_date)
                    if start_date and extracted_data.get("oldest"):
                        from datetime import datetime

                        try:
                            oldest_ts = float(extracted_data["oldest"])
                            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
                            start_ts = start_dt.timestamp()
                            if oldest_ts < start_ts:
                                logger.info(f"Reached start date {start_date}, stopping scroll")
                                break
                        except Exception as e:
                            logger.warning(f"Failed to check start date: {e}")

                    # Stop if no new messages for several attempts AND we don't have a date range to verify
                    if consecutive_no_new >= CONSECUTIVE_NO_NEW_MESSAGES_THRESHOLD:
                        if not (start_date or end_date):
                            # No date range to verify - stop based on message count alone
                            logger.info(
                                "No new messages found after multiple attempts, stopping scroll"
                            )
                            break
                        else:
                            # We have a date range - check coverage one more time before stopping
                            coverage_info = _check_date_separator_coverage(
                                mcp_evaluate_script, start_date=start_date, end_date=end_date
                            )
                            if coverage_info.get("complete"):
                                logger.info(
                                    "No new messages found, but complete day coverage confirmed. "
                                    "Stopping scroll"
                                )
                                break
                            else:
                                logger.info(
                                    "No new messages found, but day coverage incomplete. "
                                    "Continuing to scroll..."
                                )
                                # Reset counter to give more attempts for date separator detection
                                consecutive_no_new = max(0, consecutive_no_new - 2)
                else:
                    consecutive_no_new += 1

            except Exception as e:
                logger.warning(f"Error during scroll attempt {attempt + 1}: {e}")
                consecutive_no_new += 1
                if consecutive_no_new >= CONSECUTIVE_NO_NEW_MESSAGES_THRESHOLD:
                    # Check coverage before stopping on error
                    if start_date or end_date:
                        coverage_info = _check_date_separator_coverage(
                            mcp_evaluate_script, start_date=start_date, end_date=end_date
                        )
                        if coverage_info.get("complete"):
                            logger.info(
                                "Complete day coverage confirmed despite errors. Stopping scroll"
                            )
                            break
                    else:
                        break

        logger.info(
            f"Completed backward scrolling. Extracted {len(all_extracted_messages)} messages total"
        )

        # CRITICAL: After scrolling backward, scroll forward to capture any messages that
        # might have been missed. Slack's virtual scrolling can cause messages to disappear
        # from DOM when scrolling backward, so we need to scroll forward as well to ensure
        # complete coverage of the target date range.
        if start_date or end_date:
            logger.info("Scrolling forward to ensure complete message coverage...")
            forward_scroll_attempts = 0
            max_forward_attempts = 20
            consecutive_no_new_forward = 0

            for forward_attempt in range(max_forward_attempts):
                # Extract before scrolling forward
                try:
                    pre_forward_result = mcp_evaluate_script(function=script)
                    if pre_forward_result and isinstance(pre_forward_result, dict):
                        if "messages" in pre_forward_result:
                            pre_forward_messages = pre_forward_result.get("messages", [])
                        elif "result" in pre_forward_result:
                            pre_forward_messages = pre_forward_result["result"].get("messages", [])
                        else:
                            pre_forward_messages = []

                        existing_ts = {msg.get("ts") for msg in all_extracted_messages} | {
                            msg.get("ts") for msg in existing_messages
                        }
                        new_forward_count = sum(
                            1 for msg in pre_forward_messages if msg.get("ts") not in existing_ts
                        )
                        if new_forward_count > 0:
                            logger.info(
                                f"Found {new_forward_count} new messages before forward scroll (attempt {forward_attempt + 1})"
                            )
                            all_extracted_messages.extend(pre_forward_messages)
                            consecutive_no_new_forward = 0
                        else:
                            consecutive_no_new_forward += 1
                except Exception as e:
                    logger.warning(f"Failed to extract before forward scroll: {e}")

                # Scroll forward (PageDown) to load newer messages
                mcp_press_key(key="PageDown")
                time.sleep(SCROLL_KEY_DELAY_SECONDS)
                mcp_press_key(key="PageDown")
                time.sleep(SCROLL_WAIT_SECONDS)

                # Extract after scrolling forward
                try:
                    post_forward_result = mcp_evaluate_script(function=script)
                    if post_forward_result and isinstance(post_forward_result, dict):
                        if "messages" in post_forward_result:
                            post_forward_messages = post_forward_result.get("messages", [])
                        elif "result" in post_forward_result:
                            post_forward_messages = post_forward_result["result"].get(
                                "messages", []
                            )
                        else:
                            post_forward_messages = []

                        existing_ts = {msg.get("ts") for msg in all_extracted_messages} | {
                            msg.get("ts") for msg in existing_messages
                        }
                        new_post_count = sum(
                            1 for msg in post_forward_messages if msg.get("ts") not in existing_ts
                        )
                        if new_post_count > 0:
                            consecutive_no_new_forward = 0
                            logger.info(
                                f"Found {new_post_count} new messages after forward scroll (attempt {forward_attempt + 1})"
                            )
                            all_extracted_messages.extend(post_forward_messages)
                        else:
                            consecutive_no_new_forward += 1

                        # Check if we've reached the end date
                        if end_date and post_forward_result.get("latest"):
                            from datetime import datetime

                            try:
                                latest_ts = float(post_forward_result["latest"])
                                end_dt = datetime.strptime(end_date, "%Y-%m-%d")
                                end_dt = end_dt.replace(hour=23, minute=59, second=59)
                                end_ts = end_dt.timestamp()
                                if latest_ts > end_ts:
                                    logger.info(
                                        f"Reached end date {end_date}, stopping forward scroll"
                                    )
                                    break
                            except Exception as e:
                                logger.warning(f"Failed to check end date: {e}")

                        # Stop if no new messages for several attempts
                        if consecutive_no_new_forward >= 3:
                            logger.info("No new messages found after forward scrolling, stopping")
                            break
                except Exception as e:
                    logger.warning(f"Failed to extract after forward scroll: {e}")
                    consecutive_no_new_forward += 1
                    if consecutive_no_new_forward >= 3:
                        break

        logger.info(
            f"Completed all scrolling. Extracted {len(all_extracted_messages)} messages total"
        )

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

                # Optimize: avoid list concatenation by using set union
                existing_ts = {msg.get("ts") for msg in all_extracted_messages} | {
                    msg.get("ts") for msg in existing_messages
                }
                final_new_count = sum(
                    1 for msg in final_messages if msg.get("ts") not in existing_ts
                )
                if final_new_count > 0:
                    logger.info(f"Found {final_new_count} new messages in final extraction")
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

    # Save to file only if output_file is provided (optional - can output to stdout instead)
    if output_file:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        temp_file = output_file.with_suffix(output_file.suffix + ".tmp")
        try:
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(combined_result, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())  # Ensure data is written to disk

            # Atomic rename
            temp_file.replace(output_file)
            logger.info(f"Saved {len(unique_messages)} unique messages to {output_file}")
        except (OSError, IOError, PermissionError) as e:
            # Clean up temp file on error
            if temp_file.exists():
                try:
                    temp_file.unlink()
                except Exception:
                    pass
            logger.error(f"Failed to save extracted messages to {output_file}: {e}")
            raise

    logger.info(f"Date range: {combined_result['oldest']} to {combined_result['latest']}")

    # Output to stdout if requested (for piping to main.py)
    if output_to_stdout:
        json.dump(combined_result, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")  # Add newline after JSON
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
