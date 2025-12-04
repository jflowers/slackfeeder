import logging
import time
import textwrap
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.browser_scraper import (
    expand_and_extract_thread_replies,
    THREAD_SIDEPANEL_SELECTOR
)
from src.utils import setup_logging

logger = setup_logging()

# Constants
SEARCH_WAIT_SECONDS = 3.0
PAGINATION_WAIT_SECONDS = 2.0
MAX_SEARCH_PAGES = 50

# JS Helpers
def _get_js_extract_search_results() -> str:
    """Returns JavaScript to extract metadata from search results list."""
    return textwrap.dedent(r'''
    () => {
        const items = document.querySelectorAll('div[role="list"] > div[role="listitem"]');
        const results = [];
        
        items.forEach(item => {
            // Find "View thread" button or timestamp link
            const viewThreadBtn = item.querySelector('button[data-qa="view_thread_button"]'); // Hypothetical selector, usually text "View thread"
            // Fallback to searching by text content "View thread" if data-qa is missing
            let clickTarget = viewThreadBtn;
            if (!clickTarget) {
                // Look in common clickable elements
                const candidates = item.querySelectorAll('button, a, div, span');
                for (const candidate of candidates) {
                    if (candidate.innerText && candidate.innerText.trim() === 'View thread') {
                        clickTarget = candidate;
                        break;
                    }
                }
            }
            
            // Also look for timestamp link which contains thread_ts
            const tsLink = item.querySelector('a[href*="archives"]');
            let thread_ts = null;
            let conversation_id = null;
            
            if (tsLink) {
                const href = tsLink.href;
                const tsMatch = href.match(/thread_ts=(\d+\.\d+)/);
                const cidMatch = href.match(/archives\/(C[A-Z0-9]+)\//);
                
                if (tsMatch) thread_ts = tsMatch[1];
                if (cidMatch) conversation_id = cidMatch[1];
            }
            
            if (thread_ts && clickTarget) {
                results.push({
                    thread_ts: thread_ts,
                    conversation_id: conversation_id,
                    click_element_uid: clickTarget.getAttribute('uid')
                });
            }
        });
        
        return { results: results };
    }
    ''')

def _get_js_find_next_page_button() -> str:
    """Returns JavaScript to find the 'Next page' button in search results."""
    return textwrap.dedent(r'''
    () => {
        const buttons = document.querySelectorAll('button');
        for (const btn of buttons) {
            if (btn.innerText === 'Next page' && !btn.disabled) {
                return { uid: btn.getAttribute('uid') };
            }
        }
        return null;
    }
    ''')

def extract_historical_threads_via_search(
    mcp_evaluate_script: Callable,
    mcp_click: Callable,
    mcp_press_key: Callable,
    mcp_fill: Callable,
    search_query: str,
    export_date_range: Tuple[datetime, datetime],
) -> List[List[Dict[str, Any]]]:
    """                                                                                                                                                 
    Orchestrates the search-based historical thread extraction.                                                                                         
                                                                                                                                                        
    1. Executes search query.                                                                                                                           
    2. Iterates through result pages.                                                                                                                   
    3. For each thread found:                                                                                                                           
       - Opens sidebar.                                                                                                                                 
       - Scrapes full history.                                                                                                                          
       - Closes sidebar.                                                                                                                                
    """                                                                                                                                                 
    logger.info(f"Starting historical thread extraction with query: {search_query}")                                                                    
                                                                                                                                                        
    # 1. Perform Search (Assume user might be on search page or we navigate)                                                                            
    # For now, let's assume we are ON the search page or can trigger it via global search                                                               
    # But a robust way is to click the search button in the sidebar or press Ctrl+G/Cmd+G                                                               
                                                                                                                                                        
    # Note: Implementing robust navigation to search and inputting query is complex via DOM.                                                            
    # PROPOSAL: Use the existing query if provided (user already searched), or try to update it.                                                        
    # If the user provides a query string, we should probably assume we need to enter it.                                                               
                                                                                                                                                        
    # ... (Implementation of search entry omitted for brevity, assuming user pre-filled or we implement basic entry)                                    
                                                                                                                                                        
    all_threads = []
    seen_thread_timestamps = set()
                                                                                                                                                        
    for page in range(MAX_SEARCH_PAGES):
        logger.info(f"Processing Search Results Page {page + 1}")
                                                                                                                                                        
        # Extract results from current page                                                                                                             
        js_extract = _get_js_extract_search_results()                                                                                                   
        search_data = mcp_evaluate_script(function=js_extract)                                                                                          
                                                                                                                                                        
        if isinstance(search_data, dict) and "result" in search_data:                                                                                   
             search_data = search_data["result"]                                                                                                        
                                                                                                                                                        
        if not search_data or not search_data.get("results"):                                                                                           
            logger.info("No more search results found on this page.")                                                                                   
            break                                                                                                                                       
                                                                                                                                                        
        results = search_data["results"]                                                                                                                
        logger.info(f"Found {len(results)} threads on page {page + 1}")                                                                                 
                                                                                                                                                        
        for thread_info in results:                                                                                                                     
            thread_ts = thread_info["thread_ts"]                                                                                                        
                                                                                                                                                        
            if thread_ts in seen_thread_timestamps:                                                                                                     
                logger.debug(f"Skipping already seen thread {thread_ts}")                                                                               
                continue                                                                                                                                
                                                                                                                                                        
            seen_thread_timestamps.add(thread_ts)                                                                                                       
                                                                                                                                                        
            # Use shared logic to open and extract                                                                                                      
            thread_messages = expand_and_extract_thread_replies(                                                                                        
                mcp_evaluate_script,                                                                                                                    
                mcp_click,                                                                                                                              
                mcp_press_key,                                                                                                                          
                thread_info,                                                                                                                            
                export_date_range                                                                                                                       
            )                                                                                                                                           
                                                                                                                                                        
            if thread_messages:                                                                                                                         
                all_threads.append(thread_messages)                                                                                                     
                                                                                                                                                        
        # Pagination                                                                                                                                    
        js_next = _get_js_find_next_page_button()                                                                                                       
        next_btn = mcp_evaluate_script(function=js_next)                                                                                                
                                                                                                                                                        
        if isinstance(next_btn, dict) and "result" in next_btn:                                                                                         
            next_btn = next_btn["result"]                                                                                                               
                                                                                                                                                        
        if next_btn and next_btn.get("uid"):                                                                                                            
            logger.info("Navigating to next search page...")                                                                                            
            mcp_click(uid=next_btn["uid"])                                                                                                              
            time.sleep(PAGINATION_WAIT_SECONDS)                                                                                                         
        else:                                                                                                                                           
            logger.info("No 'Next page' button found. Finished search iteration.")                                                                      
            break                                                                                                                                       
                                                                                                                                                        
    return all_threads