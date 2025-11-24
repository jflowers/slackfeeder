#!/usr/bin/env python3
"""
Helper script to select a conversation from Slack sidebar using MCP chrome-devtools tools.

This script is meant to be called by an AI agent that has access to MCP chrome-devtools tools.
It provides a function that the agent can use to select conversations from the sidebar.

Usage:
    The agent should call this function with the conversation_id, and the function will
    guide the agent through the selection process using MCP chrome-devtools tools.
"""

import sys
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.utils import setup_logging

logger = setup_logging()


def select_conversation_instructions(conversation_id: str) -> dict:
    """Provide instructions for selecting a conversation from the sidebar.
    
    This function returns instructions that an AI agent can follow to select
    a conversation from the Slack sidebar using MCP chrome-devtools tools.
    
    Args:
        conversation_id: Slack conversation ID (e.g., "D06DDJ2UH2M")
        
    Returns:
        Dictionary with instructions for the agent
    """
    return {
        "conversation_id": conversation_id,
        "steps": [
            "1. Take a snapshot of the page using mcp_chrome-devtools_take_snapshot",
            "2. Search for a div element with id matching the conversation_id",
            "3. Find the parent treeitem element that contains this div",
            "4. Look for a button or link element within the treeitem",
            "5. Click on the button/link using mcp_chrome-devtools_click with the element's uid",
            "6. Wait for the conversation to load (take another snapshot to verify)",
        ],
        "javascript_helper": f"""
        // JavaScript to find the conversation element:
        () => {{
            const targetId = '{conversation_id}';
            const div = document.getElementById(targetId);
            if (!div) {{
                return {{ found: false, error: 'Div with id ' + targetId + ' not found' }};
            }}
            
            // Find parent treeitem
            const treeitem = div.closest('[role="treeitem"]');
            if (!treeitem) {{
                return {{ found: false, error: 'Parent treeitem not found' }};
            }}
            
            // Find clickable element (button or link)
            const button = treeitem.querySelector('button');
            const link = treeitem.querySelector('a[href*="/archives/"]');
            const clickable = button || link;
            
            if (!clickable) {{
                return {{ found: false, error: 'No clickable element found in treeitem' }};
            }}
            
            return {{
                found: true,
                treeitem_text: treeitem.textContent.trim().substring(0, 50),
                clickable_type: button ? 'button' : 'link',
                clickable_text: clickable.textContent.trim().substring(0, 50)
            }};
        }}
        """,
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python select_conversation_from_sidebar.py <conversation_id>")
        sys.exit(1)
    
    conversation_id = sys.argv[1]
    instructions = select_conversation_instructions(conversation_id)
    
    print(f"Instructions for selecting conversation {conversation_id}:")
    print("\nSteps:")
    for step in instructions["steps"]:
        print(f"  {step}")
    
    print("\nJavaScript helper function:")
    print(instructions["javascript_helper"])
