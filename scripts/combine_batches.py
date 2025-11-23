#!/usr/bin/env python3
"""
Combine multiple message extraction batch files into a single file.
"""

import json
import sys
from pathlib import Path
from typing import Dict, List, Any

def combine_batches(batch_files: List[Path], output_file: Path) -> Dict[str, Any]:
    """Combine multiple batch JSON files into a single deduplicated file.
    
    Args:
        batch_files: List of paths to batch JSON files
        output_file: Path to write combined output
        
    Returns:
        Combined result dictionary
    """
    all_messages = []
    seen_ts = set()
    
    for batch_file in batch_files:
        if not batch_file.exists():
            print(f"Warning: {batch_file} does not exist, skipping", file=sys.stderr)
            continue
            
        try:
            with open(batch_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                messages = data.get("messages", [])
                
                for msg in messages:
                    ts = msg.get("ts")
                    if ts and ts not in seen_ts:
                        seen_ts.add(ts)
                        all_messages.append(msg)
                        
        except Exception as e:
            print(f"Error reading {batch_file}: {e}", file=sys.stderr)
            continue
    
    # Sort by timestamp
    all_messages.sort(key=lambda m: float(m.get("ts", 0)))
    
    result = {
        "ok": True,
        "messages": all_messages,
        "message_count": len(all_messages),
        "oldest": all_messages[0].get("ts") if all_messages else None,
        "latest": all_messages[-1].get("ts") if all_messages else None,
    }
    
    # Write combined result
    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)
    
    return result

if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="Combine message extraction batches")
    parser.add_argument("batch_files", nargs="+", type=Path, help="Batch JSON files to combine")
    parser.add_argument("-o", "--output", type=Path, required=True, help="Output file path")
    
    args = parser.parse_args()
    
    result = combine_batches(args.batch_files, args.output)
    print(f"Combined {result['message_count']} messages")
    print(f"Date range: {result['oldest']} to {result['latest']}")
