#!/usr/bin/env python3
"""
Improved script to capture Slack DM API responses with better scrolling and direct XHR support.

This script provides two methods:
1. Page Down key scrolling (more reliable)
2. Direct XHR calls using extracted token (most reliable)
"""

import json
import sys
import time
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime

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
SCROLL_DELAY_SECONDS = 1.5
NETWORK_REQUEST_WAIT_SECONDS = 3.0
MAX_SCROLL_ATTEMPTS = 50
PAGE_DOWN_REPEATS = 3  # Press Page Down multiple times per attempt


def extract_token_from_request(request_data: Dict[str, Any]) -> Optional[str]:
    """Extract Slack token from a network request."""
    try:
        request_body = request_data.get("requestBody", "")
        if "token" in request_body:
            # Parse multipart form data
            for line in request_body.split("\n"):
                if "name=\"token\"" in line:
                    # Next line should contain the token
                    token_line = request_body.split(line)[1].split("\n")[1]
                    return token_line.strip()
    except Exception as e:
        logger.debug(f"Failed to extract token: {e}")
    return None


def make_direct_xhr_call(
    mcp_evaluate_script,
    channel_id: str,
    token: str,
    oldest_ts: Optional[str] = None,
    latest_ts: Optional[str] = None,
    cursor: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Make a direct XHR call to Slack's conversations.history API."""
    if not mcp_evaluate_script:
        return None
    
    # Build form data
    form_data_parts = [
        f"token={token}",
        f"channel={channel_id}",
        "limit=200",  # Request more messages per call
        "ignore_replies=true",
        "include_pin_count=false",
        "inclusive=true",
        "no_user_profile=true",
        "include_stories=true",
        "include_free_team_extra_messages=true",
        "include_date_joined=false",
    ]
    
    if oldest_ts:
        form_data_parts.append(f"oldest={oldest_ts}")
    if latest_ts:
        form_data_parts.append(f"latest={latest_ts}")
    if cursor:
        form_data_parts.append(f"cursor={cursor}")
    
    form_data = "&".join(form_data_parts)
    
    script = f"""
    (async function() {{
        try {{
            const response = await fetch('https://redhat.enterprise.slack.com/api/conversations.history', {{
                method: 'POST',
                headers: {{
                    'Content-Type': 'application/x-www-form-urlencoded',
                }},
                credentials: 'include',
                body: `{form_data}`
            }});
            
            const data = await response.json();
            return {{success: true, data: data}};
        }} catch (error) {{
            return {{success: false, error: error.message}};
        }}
    }})();
    """
    
    try:
        result = mcp_evaluate_script(function=script)
        if result and result.get("success"):
            return result.get("data")
        else:
            logger.warning(f"Direct XHR call failed: {result.get('error', 'Unknown error')}")
    except Exception as e:
        logger.error(f"Exception making direct XHR call: {e}")
    
    return None


def capture_with_page_down_keys(
    output_dir: Path,
    mcp_list_network_requests,
    mcp_get_network_request,
    mcp_press_key,
    scroll_attempts: int = MAX_SCROLL_ATTEMPTS,
) -> List[Path]:
    """Capture using Page Down keys (more reliable than JavaScript scrolling)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    captured_files = []
    seen_response_ids = set()
    response_counter = 0

    logger.info("Starting capture with Page Down keys")
    logger.info(f"Will attempt {scroll_attempts} scroll operations")
    logger.info(f"Pressing Page Down {PAGE_DOWN_REPEATS} times per attempt")

    for attempt in range(scroll_attempts):
        logger.info(f"Scroll attempt {attempt + 1}/{scroll_attempts}")

        # Press Page Down multiple times
        try:
            for i in range(PAGE_DOWN_REPEATS):
                mcp_press_key(key="PageDown")
                time.sleep(0.3)  # Small delay between key presses
        except Exception as e:
            logger.error(f"Failed to press Page Down: {e}")
            break

        # Wait for network requests
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
                continue
        except Exception as e:
            logger.error(f"Failed to list network requests: {e}")
            continue

        # Find conversations.history requests
        history_requests = find_conversations_history_requests(network_requests)

        # Process each new request
        new_captures = 0
        for req in history_requests:
            req_id = req.get("requestId") or req.get("id")
            if not req_id or req_id in seen_response_ids:
                continue

            seen_response_ids.add(req_id)

            # Get response body
            try:
                response_data = mcp_get_network_request(reqid=req_id)
                # Extract response body - structure may vary
                if isinstance(response_data, dict):
                    response_body = response_data.get("responseBody", "")
                    if isinstance(response_body, str):
                        try:
                            response_json = json.loads(response_body)
                        except json.JSONDecodeError:
                            continue
                    elif isinstance(response_body, dict):
                        response_json = response_body
                    else:
                        continue
                else:
                    continue

                # Check if response has messages
                if not response_json.get("ok") or not response_json.get("messages"):
                    continue

                # Save response
                response_file = output_dir / f"response_{response_counter}.json"
                with open(response_file, "w", encoding="utf-8") as f:
                    json.dump(response_json, f, indent=2)

                captured_files.append(response_file)
                messages_count = len(response_json.get("messages", []))
                logger.info(
                    f"Captured response {response_counter}: {messages_count} messages "
                    f"(oldest: {response_json.get('oldest')}, latest: {response_json.get('latest')})"
                )
                response_counter += 1
                new_captures += 1

            except Exception as e:
                logger.error(f"Failed to process request {req_id}: {e}")
                continue

        if new_captures == 0:
            logger.debug(f"No new captures in attempt {attempt + 1}")
            # If we haven't captured anything in a while, we might be done
            if attempt > 5:
                logger.info("No new captures for several attempts, stopping")
                break

    logger.info(f"Capture complete: {len(captured_files)} response files saved")
    return captured_files


def capture_with_direct_xhr(
    output_dir: Path,
    mcp_evaluate_script,
    channel_id: str,
    token: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> List[Path]:
    """Capture using direct XHR calls (most reliable method)."""
    output_dir.mkdir(parents=True, exist_ok=True)
    captured_files = []
    response_counter = 0

    logger.info("Starting capture with direct XHR calls")
    logger.info(f"Channel ID: {channel_id}")

    # Convert dates to timestamps if provided
    oldest_ts = None
    latest_ts = None
    if start_date:
        try:
            dt = datetime.strptime(start_date, "%Y-%m-%d")
            oldest_ts = str(dt.timestamp())
        except ValueError:
            logger.error(f"Invalid start date format: {start_date}")
            return []
    
    if end_date:
        try:
            dt = datetime.strptime(end_date, "%Y-%m-%d")
            # End of day
            dt = dt.replace(hour=23, minute=59, second=59)
            latest_ts = str(dt.timestamp())
        except ValueError:
            logger.error(f"Invalid end date format: {end_date}")
            return []

    cursor = None
    has_more = True
    page_count = 0

    while has_more:
        page_count += 1
        logger.info(f"Fetching page {page_count}...")

        response_data = make_direct_xhr_call(
            mcp_evaluate_script=mcp_evaluate_script,
            channel_id=channel_id,
            token=token,
            oldest_ts=oldest_ts,
            latest_ts=latest_ts,
            cursor=cursor,
        )

        if not response_data or not response_data.get("ok"):
            logger.error(f"Failed to fetch page {page_count}")
            break

        messages = response_data.get("messages", [])
        if not messages:
            logger.info("No more messages")
            break

        # Save response
        response_file = output_dir / f"response_{response_counter}.json"
        with open(response_file, "w", encoding="utf-8") as f:
            json.dump(response_data, f, indent=2)

        captured_files.append(response_file)
        logger.info(
            f"Captured page {page_count}: {len(messages)} messages "
            f"(oldest: {response_data.get('oldest')}, latest: {response_data.get('latest')})"
        )
        response_counter += 1

        # Check for more pages
        has_more = response_data.get("has_more", False)
        response_metadata = response_data.get("response_metadata", {})
        cursor = response_metadata.get("next_cursor")

        if not has_more or not cursor:
            break

        # Rate limiting
        time.sleep(1.0)

    logger.info(f"Direct XHR capture complete: {len(captured_files)} response files saved")
    return captured_files
