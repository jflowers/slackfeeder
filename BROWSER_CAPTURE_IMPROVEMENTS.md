# Browser Capture Script Improvements

## Summary

Updated the browser capture script (`scripts/capture_slack_dm_mcp.py`) with significant improvements to ensure all messages are captured, especially when scrolling through date ranges.

## Key Improvements

### 1. **More Aggressive Page Down Scrolling**
- **Before**: Single Page Down press per attempt
- **After**: 5 Page Down presses per attempt (configurable via `PAGE_DOWN_PRESSES_PER_ATTEMPT`)
- **Benefit**: Loads more messages per scroll attempt, reducing total time needed

### 2. **Increased Wait Times**
- **Before**: 2 seconds wait after scrolling
- **After**: 4 seconds wait after scrolling (configurable via `NETWORK_REQUEST_WAIT_SECONDS`)
- **Benefit**: Ensures network requests complete before checking for new messages

### 3. **Smart Stopping Logic**
- **New**: Tracks consecutive attempts with no new messages
- **Threshold**: Stops after 5 consecutive attempts with no new messages (configurable via `CONSECUTIVE_NO_NEW_MESSAGES_THRESHOLD`)
- **Benefit**: Prevents infinite loops and stops when all messages are captured

### 4. **End Date Detection**
- **New**: Optional `end_date_timestamp` parameter
- **Behavior**: Stops scrolling when message timestamps exceed the end date
- **Benefit**: Automatically stops at the desired date boundary

### 5. **Exponential Backoff**
- **New**: Slight delay increase between scroll attempts
- **Formula**: `SCROLL_DELAY_SECONDS * (1 + attempt * 0.01)`
- **Benefit**: Reduces chance of missing messages in large conversations

### 6. **Better Logging**
- **New**: Logs oldest/latest timestamps for each captured response
- **New**: Logs consecutive no-message attempts
- **Benefit**: Better visibility into capture progress

## Configuration Constants

All constants are defined at the top of `scripts/capture_slack_dm_mcp.py`:

```python
SCROLL_DELAY_SECONDS = 0.3  # Delay between individual Page Down presses
NETWORK_REQUEST_WAIT_SECONDS = 4.0  # Wait time for network requests after scrolling
MAX_SCROLL_ATTEMPTS = 200  # Maximum number of scroll attempts before stopping
PAGE_DOWN_PRESSES_PER_ATTEMPT = 5  # Number of Page Down presses per scroll attempt
CONSECUTIVE_NO_NEW_MESSAGES_THRESHOLD = 5  # Stop after this many attempts with no new messages
```

## Usage

The improved script is used the same way as before, but now supports an optional `end_date_timestamp` parameter:

```python
from scripts.capture_slack_dm_mcp import capture_with_mcp_tools
from src.utils import convert_date_to_timestamp

# Convert end date to timestamp (optional)
end_date_ts = None
if end_date:
    end_date_ts = float(convert_date_to_timestamp(end_date, is_end_date=True))

captured_files = capture_with_mcp_tools(
    output_dir=Path("browser_exports/api_responses"),
    scroll_attempts=200,
    mcp_list_network_requests=list_network_requests_function,
    mcp_get_network_request=get_network_request_function,
    mcp_press_key=press_key_function,
    use_keyboard_scroll=True,  # Now defaults to True (Page Down keys)
    end_date_timestamp=end_date_ts,  # Optional: stop at this timestamp
)
```

## What Changed from Previous Version

1. **Default scrolling method**: Changed from JavaScript scrolling to Page Down keys (more reliable)
2. **Parameter name**: `use_javascript_scroll` → `use_keyboard_scroll` (inverted logic)
3. **New parameter**: `end_date_timestamp` for automatic date range stopping
4. **Better error handling**: Tracks consecutive failures and stops gracefully
5. **Improved message tracking**: Tracks latest message timestamp for better progress visibility

## Testing Recommendations

When testing the improved capture script:

1. **Start from the beginning**: Navigate to the start date (e.g., Nov 1st) before starting capture
2. **Monitor logs**: Watch for "No new messages captured" messages - if you see 5 consecutive, it will stop
3. **Check timestamps**: Verify that captured messages span the full date range
4. **Adjust constants if needed**: If messages are still missing, try:
   - Increasing `PAGE_DOWN_PRESSES_PER_ATTEMPT` to 7-10
   - Increasing `NETWORK_REQUEST_WAIT_SECONDS` to 5-6 seconds
   - Increasing `CONSECUTIVE_NO_NEW_MESSAGES_THRESHOLD` to 7-10

## Troubleshooting

### Issue: Still missing messages from certain dates
- **Solution**: Increase `PAGE_DOWN_PRESSES_PER_ATTEMPT` and `NETWORK_REQUEST_WAIT_SECONDS`
- **Alternative**: Manually scroll through the date range while the script captures

### Issue: Script stops too early
- **Solution**: Increase `CONSECUTIVE_NO_NEW_MESSAGES_THRESHOLD`
- **Check**: Verify network requests are being captured (check browser DevTools)

### Issue: Script runs too long
- **Solution**: Use `end_date_timestamp` parameter to stop at a specific date
- **Alternative**: Reduce `MAX_SCROLL_ATTEMPTS` if you know the approximate number needed

## Next Steps

1. Test the improved script with a known date range (e.g., Nov 1-18, 2025)
2. Verify all messages are captured by checking the processed output
3. Adjust constants based on your specific Slack workspace's behavior
4. Consider integrating the `end_date_timestamp` parameter into the main export workflow
