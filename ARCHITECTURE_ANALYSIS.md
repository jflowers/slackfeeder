# Architecture Analysis: Export Methods and Duplication

## Current Architecture

### Export Methods

1. **`--export-history`**: Slack API-based export
   - Fetches messages via Slack API (`SlackClient.fetch_channel_history`)
   - Processes messages with `preprocess_history()`
   - Groups by date with `group_messages_by_date()`
   - Optionally uploads to Google Drive (when `--upload-to-drive` is set)
   - Shares folders with `share_folder_with_members()`

2. **`--browser-export-dm`**: Browser DOM-based export
   - Extracts messages from browser DOM (via stdin JSON)
   - Processes messages with `preprocess_history(use_display_names=True)`
   - Groups by date with `group_messages_by_date()`
   - Optionally uploads to Google Drive (when `--upload-to-drive` is set)
   - Shares folders with `share_folder_for_browser_export()`

3. **`--upload-to-drive`**: Flag (not a separate method)
   - Can be used with either `--export-history` or `--browser-export-dm`
   - Enables Google Drive upload functionality

## Identified Duplication

### 1. Google Drive Upload Logic (High Duplication)

**Location**: Lines ~1378-1509 (`--export-history`) vs Lines ~2246-2443 (`--browser-export-dm`)

**Duplicated Code:**
- Grouping messages by date (`group_messages_by_date`)
- Creating/getting Google Drive folder
- Iterating through daily groups
- Checking if doc exists
- Creating metadata headers (nearly identical)
- Creating/updating Google Docs (`create_or_update_google_doc`)
- Saving export metadata (`save_export_metadata`)
- Statistics tracking

**Differences:**
- Browser export uses `preprocess_history(use_display_names=True)` vs API export uses `preprocess_history(slack_client, people_cache)`
- Browser export has "[Browser Export - No ID]" in metadata header vs actual channel ID
- Browser export initializes Google Drive client conditionally (for incremental export check)

**Recommendation**: Extract to `upload_messages_to_drive()` function

### 2. Incremental Export Logic (Medium Duplication)

**Location**: Lines ~1282-1318 (`--export-history`) vs Lines ~2105-2205 (`--browser-export-dm`)

**Duplicated Code:**
- Checking for explicit `--start-date`
- Checking Google Drive for last export timestamp
- Using later of explicit date or last export
- Logging incremental export status

**Differences:**
- Browser export has more complex logic for conditional Google Drive client initialization
- Browser export handles the case where Google Drive client might already be initialized

**Recommendation**: Extract to `get_oldest_timestamp_for_export()` function

### 3. Date Range Filtering (Medium Duplication)

**Location**: Lines ~1320-1343 (`--export-history`) vs Lines ~2207-2244 (`--browser-export-dm`)

**Duplicated Code:**
- Converting end date to timestamp
- Validating date range (start < end)
- Filtering messages by date range
- Logging filtered counts

**Differences:**
- API export validates date range against MAX_DATE_RANGE_DAYS
- Browser export doesn't validate against MAX_DATE_RANGE_DAYS (could be intentional)

**Recommendation**: Extract to `filter_messages_by_date_range()` function

### 4. Folder Sharing Logic (Medium Duplication)

**Location**: `share_folder_with_members()` vs `share_folder_for_browser_export()`

**Duplicated Code:**
- Checking `share` flag
- Getting conversation members
- Handling `shareMembers` list
- Checking opt-out preferences (`no_share_set`, `no_notifications_set`)
- Revoking access for removed members
- Sharing with current members
- Rate limiting
- Statistics tracking

**Differences:**
- Browser export function gets members differently (DM vs group DM logic)
- Browser export function takes `conversation_info` dict vs separate parameters
- Browser export function doesn't take `sanitized_folder_name` parameter

**Recommendation**: Consolidate into single function with unified member retrieval logic

### 5. Statistics Logging (Low Duplication)

**Location**: Lines ~1111-1129 (`_log_statistics`) vs Lines ~2434-2443 (`--browser-export-dm`)

**Duplicated Code:**
- Statistics dictionary structure
- Logging format

**Differences:**
- Browser export has inline logging vs dedicated function
- Browser export checks for `'shared'` key existence

**Recommendation**: Use `_log_statistics()` function consistently

## Simplification Opportunities

### High Priority

1. **Extract Google Drive Upload Function**
   ```python
   def upload_messages_to_drive(
       messages: List[Dict],
       conversation_name: str,
       conversation_id: Optional[str],
       google_drive_client: GoogleDriveClient,
       google_drive_folder_id: Optional[str],
       slack_client: Optional[SlackClient],
       people_cache: Optional[Dict],
       use_display_names: bool = False,
       stats: Optional[Dict] = None
   ) -> Dict[str, int]:
       """Unified function for uploading messages to Google Drive."""
   ```

2. **Extract Incremental Export Timestamp Logic**
   ```python
   def get_oldest_timestamp_for_export(
       google_drive_client: Optional[GoogleDriveClient],
       folder_id: Optional[str],
       conversation_name: str,
       explicit_start_date: Optional[str],
       upload_to_drive: bool
   ) -> Optional[str]:
       """Get oldest timestamp for incremental export."""
   ```

3. **Consolidate Folder Sharing**
   ```python
   def share_folder_with_conversation_members(
       google_drive_client: GoogleDriveClient,
       folder_id: str,
       slack_client: SlackClient,
       conversation_id: str,
       conversation_name: str,
       conversation_info: Dict[str, Any],
       no_notifications_set: set,
       no_share_set: set,
       stats: Dict[str, int]
   ) -> None:
       """Unified folder sharing function for both export methods."""
   ```

### Medium Priority

4. **Extract Date Range Filtering**
   ```python
   def filter_messages_by_date_range(
       messages: List[Dict],
       oldest_ts: Optional[str],
       latest_ts: Optional[str],
       validate_range: bool = True
   ) -> List[Dict]:
       """Filter messages by date range."""
   ```

5. **Standardize Statistics Dictionary**
   - Create a function to initialize stats dict
   - Use `_log_statistics()` consistently

### Low Priority

6. **Unify Message Processing**
   - Both use `preprocess_history()` but with different parameters
   - Could create wrapper function that handles both cases

## Benefits of Refactoring

1. **Reduced Code Duplication**: ~400-500 lines of duplicated code could be reduced to ~200 lines of shared functions
2. **Easier Maintenance**: Bug fixes and improvements only need to be made once
3. **Consistency**: Both export methods will behave identically for shared operations
4. **Testability**: Shared functions can be unit tested independently
5. **Readability**: Main export logic becomes clearer and easier to understand

## Risks and Considerations

1. **Breaking Changes**: Refactoring could introduce bugs if not carefully tested
2. **Complexity**: Some differences between methods may be intentional (e.g., browser export doesn't validate date range)
3. **Testing**: Need to ensure all existing tests still pass after refactoring
4. **Backward Compatibility**: Must maintain same command-line interface and behavior

## Recommended Approach

1. **Phase 1**: Extract Google Drive upload logic (highest duplication)
2. **Phase 2**: Extract incremental export timestamp logic
3. **Phase 3**: Consolidate folder sharing functions
4. **Phase 4**: Extract date range filtering
5. **Phase 5**: Standardize statistics handling

Each phase should:
- Be done incrementally
- Include comprehensive tests
- Maintain backward compatibility
- Be reviewed before moving to next phase
