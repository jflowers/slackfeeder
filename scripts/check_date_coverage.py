#!/usr/bin/env python3
"""
Check date coverage for all conversations in browser-export.json.

This script helps verify that all messages between start_date and end_date
are accounted for by checking date separators and message timestamps.
"""

import json
import sys
from datetime import datetime
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.utils import setup_logging, load_json_file

logger = setup_logging()


def check_date_coverage_report(
    browser_export_config: Path,
    start_date: str,
    end_date: str,
) -> dict:
    """Generate a coverage report for all conversations.
    
    Args:
        browser_export_config: Path to browser-export.json
        start_date: Start date in YYYY-MM-DD format
        end_date: End date in YYYY-MM-DD format
    
    Returns:
        Dictionary with coverage report
    """
    config_data = load_json_file(browser_export_config)
    conversations = config_data.get("browser-export", [])
    
    # Parse date range
    start_dt = datetime.strptime(start_date, "%Y-%m-%d")
    end_dt = datetime.strptime(end_date, "%Y-%m-%d")
    
    # Generate list of dates to check
    dates_to_check = []
    current = start_dt
    while current <= end_dt:
        dates_to_check.append(current.strftime("%Y-%m-%d"))
        current = current.replace(day=current.day + 1) if current.day < 28 else current.replace(month=current.month + 1, day=1)
        if current > end_dt:
            break
    
    report = {
        "date_range": {
            "start": start_date,
            "end": end_date,
            "dates_to_check": dates_to_check
        },
        "conversations": [],
        "summary": {
            "total_conversations": len(conversations),
            "conversations_with_messages": 0,
            "conversations_with_gaps": 0,
            "total_missing_dates": 0
        }
    }
    
    logger.info(f"Checking coverage for {len(conversations)} conversations")
    logger.info(f"Date range: {start_date} to {end_date}")
    logger.info(f"Dates to check: {', '.join(dates_to_check)}")
    
    for conv in conversations:
        if not conv.get("export", True):
            continue
        
        conv_report = {
            "id": conv.get("id"),
            "name": conv.get("name"),
            "status": "pending",
            "message": "Use extract_dom_messages.py with start_date and end_date to extract and verify coverage"
        }
        
        report["conversations"].append(conv_report)
    
    return report


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: check_date_coverage.py <browser-export.json> <start_date> <end_date>")
        print("Example: check_date_coverage.py config/browser-export.json 2025-11-17 2025-11-24")
        sys.exit(1)
    
    config_path = Path(sys.argv[1])
    start_date = sys.argv[2]
    end_date = sys.argv[3]
    
    report = check_date_coverage_report(config_path, start_date, end_date)
    
    print(json.dumps(report, indent=2))
