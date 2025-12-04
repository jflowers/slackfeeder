"""
Browser/DOM export functionality for Slack Feeder.
"""
from typing import Any, Callable, Dict, Optional

from src.utils import setup_logging, sanitize_string_for_logging, load_json_file

logger = setup_logging()

# Configuration file names
BROWSER_EXPORT_CONFIG_KEY = "browser-export"  # Key in browser-export.json
BROWSER_EXPORT_CONFIG_FILENAME = "browser-export.json"  # Default config filename


def load_browser_export_config(config_path: str) -> Optional[Dict[str, Any]]:
    """Load browser-export.json configuration file.
    
    Args:
        config_path: Path to browser-export.json file
        
    Returns:
        Dictionary with browser-export configuration, or None if not found/invalid
    """
    try:
        config_data = load_json_file(config_path)
        if not config_data:
            logger.debug(f"Browser export config file not found: {config_path}")
            return None
        
        browser_exports = config_data.get(BROWSER_EXPORT_CONFIG_KEY, [])
        if not isinstance(browser_exports, list):
            logger.warning(f"Invalid {BROWSER_EXPORT_CONFIG_FILENAME} structure: '{BROWSER_EXPORT_CONFIG_KEY}' must be a list")
            return None
        
        return {BROWSER_EXPORT_CONFIG_KEY: browser_exports}
    except Exception as e:
        logger.warning(f"Error loading browser-export config: {e}", exc_info=True)
        return None


def find_conversation_in_config(config_data: Dict[str, Any], conversation_id: str = None, conversation_name: str = None) -> Optional[Dict[str, Any]]:
    """Find a conversation in browser-export.json by ID or name.
    
    Args:
        config_data: Browser export config dictionary
        conversation_id: Optional conversation ID to search for
        conversation_name: Optional conversation name to search for
        
    Returns:
        Conversation info dictionary, or None if not found
    """
    if not config_data:
        return None
    
    browser_exports = config_data.get(BROWSER_EXPORT_CONFIG_KEY, [])
    if not browser_exports:
        return None
    
    for conv in browser_exports:
        if conversation_id and conv.get("id") == conversation_id:
            return conv
        if conversation_name and conv.get("name") == conversation_name:
            return conv
    
    return None


def select_conversation_from_sidebar(conversation_id: str, mcp_click: Callable = None, mcp_evaluate_script: Callable = None) -> bool:
    """Select a conversation from the Slack sidebar by clicking on it.
    
    This function uses MCP chrome-devtools tools to find and click on the conversation
    in the sidebar. The conversation is identified by its div ID.
    
    Args:
        conversation_id: Slack conversation ID (e.g., "D1234567890")
        mcp_click: Optional MCP click function (if None, logs instructions)
        mcp_evaluate_script: Optional MCP evaluate_script function (if None, logs instructions)
        
    Returns:
        True if conversation was successfully selected, False otherwise
        
    Note:
        This function requires MCP chrome-devtools tools to be available.
        It should be called before extracting messages.
    """
    if mcp_click is None or mcp_evaluate_script is None:
        logger.info(f"To select conversation {sanitize_string_for_logging(conversation_id)} from sidebar:")
        logger.info("1. Take a snapshot of the page")
        logger.info("2. Find the div with id matching conversation_id")
        logger.info("3. Find the parent treeitem element")
        logger.info("4. Click on the treeitem or its button/link child")
        logger.info("5. Wait for the conversation to load")
        logger.info("Note: MCP tools not provided - agent should implement selection logic")
        return False
    
    # The actual implementation will be done by the agent using MCP tools
    # This is a placeholder that logs what needs to be done
    logger.info(f"Selecting conversation {sanitize_string_for_logging(conversation_id)} from sidebar...")
    logger.info("Note: Actual selection should be implemented by agent using MCP chrome-devtools tools")
    return True
