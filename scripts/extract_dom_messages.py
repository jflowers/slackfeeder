#!/usr/bin/env python3
"""
Extract messages from Slack DOM using chrome-devtools MCP.

This script extracts messages directly from the DOM of a Slack conversation
that's currently visible in the browser. It can be called interactively
with MCP tools available.

Usage:
    This script is designed to be called from Cursor with MCP chrome-devtools tools.
    It will extract messages from the currently visible page and save them.
"""

import json
import sys
from pathlib import Path
from typing import Any, Dict

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.browser_scraper import extract_messages_from_dom_script
from src.utils import setup_logging

logger = setup_logging()


def extract_and_save_dom_messages(
    output_file: Path,
    mcp_evaluate_script,
    append: bool = False,
) -> Dict[str, Any]:
    """Extract messages from DOM and save to file.

    Args:
        output_file: Path to save extracted messages
        mcp_evaluate_script: MCP function to evaluate JavaScript
        append: If True, append to existing file; if False, overwrite

    Returns:
        Dictionary with extraction results
    """
    logger.info(f"Extracting messages from DOM...")
    
    script = extract_messages_from_dom_script()
    
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
        
        message_count = len(extracted_data.get("messages", []))
        logger.info(f"Extracted {message_count} messages from DOM")
        
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
        
        # Combine messages
        all_messages = existing_messages + extracted_data.get("messages", [])
        
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
        
    except Exception as e:
        logger.error(f"Failed to extract messages from DOM: {e}", exc_info=True)
        return {"ok": False, "messages": [], "message_count": 0}


if __name__ == "__main__":
    # This script is designed to be called interactively with MCP tools
    # Example usage from Cursor:
    # from scripts.extract_dom_messages import extract_and_save_dom_messages
    # result = extract_and_save_dom_messages(
    #     Path("browser_exports/response_dom_extraction.json"),
    #     mcp_chrome-devtools_evaluate_script
    # )
    logger.info("This script should be imported and called with MCP tools")
    logger.info("Example: extract_and_save_dom_messages(output_file, mcp_evaluate_script)")
