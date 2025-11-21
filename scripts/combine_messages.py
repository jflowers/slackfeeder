#!/usr/bin/env python3
"""
Combine new messages with existing messages.

This script is used during DOM extraction to incrementally combine newly extracted
messages with previously extracted messages. It handles deduplication, sorting, and
saves the combined result back to browser_exports/response_dom_extraction.json.

Usage:
    python3 scripts/combine_messages.py '{"ok":true,"messages":[...]}'
    
    Or pipe JSON via stdin:
    echo '{"ok":true,"messages":[...]}' | python3 scripts/combine_messages.py

The script:
- Loads existing messages from browser_exports/response_dom_extraction.json
- Adds new messages (deduplicates by timestamp)
- Sorts all messages by timestamp
- Saves back to browser_exports/response_dom_extraction.json
- Prints progress information (added count, total, date range)

This is used by the Cursor Agent during DOM extraction workflows.
See DOM_EXTRACTION_GUIDE.md for more information.
"""
import json
import sys
from pathlib import Path
from datetime import datetime

output_file = Path("browser_exports/response_dom_extraction.json")

# Load existing
existing_data = json.load(open(output_file)) if output_file.exists() else {"messages": []}
existing_messages = existing_data.get("messages", [])
existing_ts_set = {msg["ts"] for msg in existing_messages if msg.get("ts")}

# Read new messages from stdin (JSON)
if len(sys.argv) > 1:
    new_messages_json = sys.argv[1]
else:
    new_messages_json = sys.stdin.read()

new_data = json.loads(new_messages_json)
new_messages = new_data.get("messages", [])

# Combine and deduplicate
all_messages = existing_messages.copy()
added_count = 0
for msg in new_messages:
    ts = msg.get("ts")
    if ts and ts not in existing_ts_set:
        all_messages.append(msg)
        existing_ts_set.add(ts)
        added_count += 1

# Sort by timestamp
all_messages.sort(key=lambda m: float(m.get("ts", 0)))

# Save
result = {
    "ok": True,
    "messages": all_messages,
    "message_count": len(all_messages),
    "oldest": all_messages[0].get("ts") if all_messages else None,
    "latest": all_messages[-1].get("ts") if all_messages else None,
    "has_more": False,
}

with open(output_file, "w") as f:
    json.dump(result, f, indent=2, ensure_ascii=False)

print(f"Added {added_count} new messages")
print(f"Total: {len(all_messages)} messages")
if all_messages:
    oldest_dt = datetime.fromtimestamp(float(all_messages[0]["ts"]))
    latest_dt = datetime.fromtimestamp(float(all_messages[-1]["ts"]))
    print(f"Date range: {oldest_dt} to {latest_dt}")
    
    target_dt = datetime(2024, 1, 3)
    if oldest_dt > target_dt:
        days_needed = int((float(all_messages[0]["ts"]) - target_dt.timestamp()) / 86400)
        print(f"Still need to scroll back {days_needed} days to reach January 3, 2024")
