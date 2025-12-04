import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
from pathlib import Path

from src.browser_scraper import (
    extract_messages_from_dom, 
    extract_messages_from_dom_script,
    expand_and_extract_thread_replies,
    THREAD_SIDEPANEL_SELECTOR,
    THREADS_SIDEBAR_BUTTON_UID,
    CLICK_WAIT_SECONDS,
    SCROLL_WAIT_SECONDS
)
from src.utils import setup_logging

logger = setup_logging()

import textwrap

# Helper JavaScript functions (defined as raw strings for correct escaping)

def _get_js_extract_thread_summary_metadata() -> str:
    """Returns JavaScript code to extract thread summary metadata."""
    return textwrap.dedent(r'''
    (targetConvName, startTimestamp, endTimestamp, threadSummarySelector) => {
        const threadItems = document.querySelectorAll(threadSummarySelector);
        const threads = [];

        threadItems.forEach(item => {
            const textContent = item.textContent;
            const link = item.querySelector('a[href*="thread_ts"]');
            
            if (link) {
                const threadTsMatch = link.href.match(/thread_ts=(\d+\.\d+)/);
                const cidMatch = link.href.match(/cid=(C|D|G)[A-Z0-9]+/);

                if (threadTsMatch && cidMatch) {
                    const thread_ts = threadTsMatch[1];
                    const conversation_id = cidMatch[0].replace('cid=', '');
                    
                    let lastReplyTimestamp = null;
                    const tsLink = item.querySelector('a[href*="/archives/"]');
                    if (tsLink) {
                         const tsMatch = tsLink.href.match(/p(\d+\.\d+)/);
                         if (tsMatch) {
                             lastReplyTimestamp = parseFloat(tsMatch[1]);
                         }
                    }

                    let is_relevant_conversation = false;
                    if (targetConvName && targetConvName.length > 0) {
                        // Simple check: does the card text contain parts of the conversation name?
                        // More robust could involve tokenizing and matching
                        if (textContent.includes(targetConvName.split(',')[0].trim()) || textContent.includes(targetConvName.split(',')[1].trim())) {
                            is_relevant_conversation = true;
                        }
                    } else {
                        // If no targetConvName, consider all threads relevant (e.g., for general export)
                        is_relevant_conversation = true;
                    }

                    const is_active_today_or_yesterday = lastReplyTimestamp && lastReplyTimestamp >= startTimestamp && lastReplyTimestamp <= endTimestamp;

                    if (is_relevant_conversation && is_active_today_or_yesterday) {
                        threads.push({
                            thread_ts: thread_ts,
                            conversation_id: conversation_id,
                            last_reply_ts: lastReplyTimestamp,
                            title_snippet: textContent.split('replied to:')[0].trim(),
                            click_element_uid: item.querySelector('a, button') ? item.querySelector('a, button').getAttribute('uid') : null
                        });
                    }
                }
            }
        });
        return { threads: threads };
    }
    ''')

# Constants
PAGE_DOWN_PRESSES_PER_ATTEMPT = 2 # Number of PageDown presses when scrolling threads list
MAX_THREADS_SCROLL_ATTEMPTS = 50 # Max attempts to scroll the threads list

# DOM Selectors
THREAD_SUMMARY_LIST_ITEM_SELECTOR = "div[role='listitem']" # Selector for individual thread summary cards


def navigate_to_threads_view(mcp_click: Callable) -> bool:
    """Navigates to the 'Threads' view in Slack.

    Args:
        mcp_click: MCP function to click on elements.

    Returns:
        True if navigation was successful, False otherwise.
    """
    logger.info("Navigating to 'Threads' view...")
    try:
        mcp_click(uid=THREADS_SIDEBAR_BUTTON_UID)
        time.sleep(CLICK_WAIT_SECONDS)
        logger.info("Successfully clicked 'Threads' sidebar button.")
        return True
    except Exception as e:
        logger.error(f"Failed to navigate to 'Threads' view: {e}", exc_info=True)
        return False


def extract_thread_summary_metadata(mcp_evaluate_script: Callable, target_conversation_name: str, export_date_range: Tuple[datetime, datetime]) -> List[Dict[str, Any]]:
    """Extracts metadata from thread summary cards in the 'Threads' view.

    Args:
        mcp_evaluate_script: MCP function to evaluate JavaScript.
        target_conversation_name: The display name of the conversation we are interested in.
        export_date_range: Tuple of (start_datetime, end_datetime) for filtering active threads.

    Returns:
        A list of dictionaries, each containing metadata for a relevant thread.
    """
    logger.info("Extracting thread summary metadata from 'Threads' view...")
    
    start_dt, end_dt = export_date_range

    js_script = _get_js_extract_thread_summary_metadata()
    # Pass the parameters as arguments to the JavaScript function
    result = mcp_evaluate_script(
        function=js_script,
        args=[
            {"targetConvName": target_conversation_name},
            {"startTimestamp": int(start_dt.timestamp())},
            {"endTimestamp": int(end_dt.timestamp())},
            {"threadSummarySelector": THREAD_SUMMARY_LIST_ITEM_SELECTOR}
        ]
    )
    
    if result and isinstance(result, dict) and 'threads' in result:
        logger.info(f"Found {len(result['threads'])} active threads in view.")
        return result['threads']
    
    logger.warning("No thread summaries extracted or unexpected script result.")
    return []


def extract_active_threads_for_daily_export(
    mcp_evaluate_script: Callable,
    mcp_click: Callable,
    mcp_press_key: Callable,
    target_conversation_name: str,
    export_date: datetime,
) -> List[Dict[str, Any]]:
    """Extracts all active threads (root + replies) for today and yesterday from the 'Threads' view.

    Args:
        mcp_evaluate_script: MCP function to evaluate JavaScript.
        mcp_click: MCP function to click on elements.
        mcp_press_key: MCP function to press keys.
        target_conversation_name: The display name of the conversation to filter by.
        export_date: The central date for the export (messages from this day and previous day).

    Returns:
        A list of dictionaries, where each dictionary represents a complete thread
        and contains its root message and all replies active within the export window.
    """
    logger.info(f"Starting active thread extraction for {target_conversation_name} for {export_date.strftime('%Y-%m-%d')} and yesterday.")

    # Calculate date range for filtering: today and yesterday
    today_start = export_date.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=timezone.utc)
    yesterday_start = today_start - timedelta(days=1)
    export_end = export_date.replace(hour=23, minute=59, second=59, microsecond=999999, tzinfo=timezone.utc)
    
    export_date_range = (yesterday_start, export_end) # Include yesterday as well
    logger.info(f"Export date range for threads: {yesterday_start.strftime('%Y-%m-%d')} to {export_end.strftime('%Y-%m-%d')}")

    # Navigate to the 'Threads' view
    if not navigate_to_threads_view(mcp_click):
        return []
    
    all_collected_threads_messages = []
    seen_thread_timestamps = set() # To prevent duplicate full threads

    consecutive_no_new_threads = 0

    for attempt in range(MAX_THREADS_SCROLL_ATTEMPTS):
        logger.info(f"Scanning 'Threads' view for active threads (attempt {attempt + 1}/{MAX_THREADS_SCROLL_ATTEMPTS})...")
        
        # Extract thread summaries from the current view
        thread_summaries = extract_thread_summary_metadata(
            mcp_evaluate_script, target_conversation_name, export_date_range
        )
        
        new_threads_found_in_this_view = 0
        for summary in thread_summaries:
            thread_ts = summary['thread_ts']
            if thread_ts not in seen_thread_timestamps:
                new_threads_found_in_this_view += 1
                seen_thread_timestamps.add(thread_ts)
                logger.info(f"Found new active thread: {summary['title_snippet']} (TS: {thread_ts})")
                
                # Expand and extract full thread messages
                # Reuse the logic from browser_scraper
                full_thread_messages = expand_and_extract_thread_replies(
                    mcp_evaluate_script, mcp_click, mcp_press_key, summary, export_date_range
                )
                
                if full_thread_messages:
                    all_collected_threads_messages.extend(full_thread_messages)
        
        if new_threads_found_in_this_view > 0:
            consecutive_no_new_threads = 0
            logger.info(f"Discovered {new_threads_found_in_this_view} new relevant threads in this view.")
        else:
            consecutive_no_new_threads += 1
            logger.info(f"No new relevant threads found in this view ({consecutive_no_new_threads}/{MAX_THREADS_SCROLL_ATTEMPTS}).")
        
        # Check if we should stop scrolling (e.g., no new threads found in multiple attempts)
        # Or if the oldest thread in view is already older than our target export_date_range
        if consecutive_no_new_threads >= 5: # Stop after 5 consecutive views with no new relevant threads
             logger.info("No new relevant threads found for several consecutive views. Assuming end of relevant threads.")
             break
        
        # Scroll down to load more threads (Threads view scrolls down for older threads)
        logger.debug(f"Scrolling down 'Threads' view to find older threads...")
        for _ in range(PAGE_DOWN_PRESSES_PER_ATTEMPT):
            mcp_press_key(key="PageDown")
            time.sleep(0.3)
        time.sleep(SCROLL_WAIT_SECONDS)
    
    logger.info(f"Finished scanning 'Threads' view. Total unique thread messages collected: {len(all_collected_threads_messages)}")

    return all_collected_threads_messages