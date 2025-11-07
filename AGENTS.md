# AGENTS.md - AI Agent Guide for Slack Feeder

This document helps AI agents understand the codebase structure, conventions, and how to work with this project effectively.

## Project Overview

**Slack Feeder** exports conversations from Slack, processes them into human-readable format, and uploads them to Google Drive. It's designed for:
- Exporting Slack channel/DM/group chat history
- Processing messages into readable text with human-readable timestamps
- Uploading to Google Drive with automatic folder organization
- Sharing folders with conversation participants
- Running in CI/CD pipelines (stateless design)

## Architecture

### Core Components

1. **`src/main.py`** - Main entry point and orchestration
   - Handles command-line arguments
   - Manages export workflow (chunked vs single file)
   - Coordinates SlackClient and GoogleDriveClient
   - Implements bulk export logic with monthly chunking

2. **`src/slack_client.py`** - Slack API integration
   - `SlackClient` class for all Slack API interactions
   - User/channel information fetching
   - Message history retrieval with pagination
   - Rate limiting and retry logic
   - User cache management

3. **`src/google_drive.py`** - Google Drive API integration
   - `GoogleDriveClient` class for Drive operations
   - Authentication and token management
   - Folder/file creation and upload
   - Permission management (share/revoke)
   - Metadata tracking for incremental exports

4. **`src/utils.py`** - Utility functions
   - Logging setup
   - JSON file I/O
   - Name sanitization (filename/foldername)
   - Date/timestamp conversions
   - Data validation (emails, channel IDs, etc.)

### Key Design Patterns

- **Stateless operation**: State is stored in Google Drive metadata files, not locally
- **Incremental exports**: Tracks last export timestamp in Drive to fetch only new messages
- **Rate limiting**: Built-in rate limiting for both Slack and Google Drive APIs
- **Error handling**: Comprehensive error handling with retries and exponential backoff
- **Security**: Token files require 600 permissions, paths sanitized in logs

## File Structure

```
slackfeeder/
??? src/                    # Source code
?   ??? main.py            # Entry point, export orchestration
?   ??? slack_client.py    # Slack API client
?   ??? google_drive.py    # Google Drive API client
?   ??? utils.py           # Utility functions
??? tests/                 # Test suite
?   ??? test_main.py       # Tests for main.py functions
?   ??? test_slack_client.py
?   ??? test_google_drive.py
?   ??? test_utils.py
??? config/                # Configuration files
?   ??? channels.json      # Channel export configuration (not in git)
?   ??? people.json        # User cache (not in git)
??? pyproject.toml         # Dependencies (source of truth)
??? requirements.txt       # Generated from pyproject.toml
??? Makefile              # Convenient commands
??? scripts/              # Helper scripts
    ??? update_requirements.sh
```

## Development Conventions

### Code Formatting

- **Black**: Line length 100, Python 3.9+
- **isort**: Import sorting, Black-compatible profile
- **pylint**: Linting with project-specific config

**Commands:**
```bash
black src/ tests/
isort src/ tests/
pylint src/ tests/
```

### Testing

- **Framework**: pytest
- **Coverage**: 156 tests covering all major functionality
- **Run tests**: `pytest` or `make test`
- **Test structure**: Mirror src/ structure in tests/

**Important**: Always run tests after making changes:
```bash
pytest
```

### Dependencies

- **Source of truth**: `pyproject.toml` (PEP 621 format)
- **Backward compatibility**: `requirements.txt` (generated)
- **To update**: Edit `pyproject.toml`, then run `./scripts/update_requirements.sh`

**Key dependencies:**
- `slack-sdk` - Slack API client
- `google-api-python-client` - Google Drive API
- `google-auth-oauthlib` - OAuth authentication
- `python-dotenv` - Environment variable loading
- `cachetools` - LRU cache for user info

## Common Tasks

### Adding a New Feature

1. **Update code** in appropriate `src/` file
2. **Add tests** in corresponding `tests/` file
3. **Format code**: `black src/ tests/` and `isort src/ tests/`
4. **Run tests**: `pytest` (ensure all pass)
5. **Update documentation** if needed (README.md)

### Modifying Dependencies

1. **Edit `pyproject.toml`** - Add/update dependencies in `[project.dependencies]` or `[project.optional-dependencies.dev]`
2. **Regenerate requirements.txt**: `./scripts/update_requirements.sh` or `make requirements`
3. **Test installation**: `pip install -e ".[dev]"`

### Adding a New Command-Line Argument

1. **Add argument** in `src/main.py` `argparse` setup
2. **Handle argument** in `main()` function
3. **Add tests** in `tests/test_main.py` or `tests/test_main_fixes.py`
4. **Update README.md** documentation

### Modifying API Integration

- **Slack API**: Edit `src/slack_client.py`
  - Follow existing rate limiting patterns
  - Use retry logic for transient errors
  - Cache user info to reduce API calls

- **Google Drive API**: Edit `src/google_drive.py`
  - Use `_rate_limit()` before API calls
  - Handle `HttpError` exceptions properly
  - Maintain secure token file permissions

## Important Patterns

### Error Handling

```python
try:
    # API call
except SlackApiError as e:
    # Handle Slack-specific errors
    # Check for rate limiting, retry if needed
except HttpError as e:
    # Handle Google Drive errors
except Exception as e:
    # Log with exc_info=True for debugging
    logger.error(f"Unexpected error: {e}", exc_info=True)
```

### Rate Limiting

- **Slack**: Use `DEFAULT_RATE_LIMIT_DELAY` between calls
- **Google Drive**: Call `self._rate_limit()` before API operations
- **Sharing**: Use `SHARE_RATE_LIMIT_INTERVAL` and `SHARE_RATE_LIMIT_DELAY`

### Logging

- Use `logger.info()` for normal operations
- Use `logger.warning()` for recoverable issues
- Use `logger.error()` for failures
- Use `logger.debug()` for detailed debugging info
- Sanitize sensitive data (paths, tokens) in logs

### Security

- **Token files**: Must have permissions 600 (owner read/write only)
- **Paths**: Sanitize file paths in log messages using `sanitize_path_for_logging()`
- **Tokens**: Don't expose token characters in error messages
- **Credentials**: Never commit credential files to git

## Testing Patterns

### Mocking External APIs

```python
@patch('src.slack_client.WebClient')
def test_something(mock_webclient):
    mock_client = mock_webclient.return_value
    mock_client.conversations_history.return_value = {"messages": [...]}
    # Test code
```

### Testing File Operations

- Use temporary directories: `tempfile.mkdtemp()`
- Clean up after tests
- Test both success and failure paths

### Testing Error Handling

- Test rate limiting scenarios
- Test API error responses
- Test invalid input validation
- Test permission errors

## CI/CD Considerations

- **Stateless**: No local state files - everything stored in Google Drive
- **Token setup**: Requires `--setup-drive-auth` run locally first
- **Environment variables**: All configuration via env vars
- **Dependencies**: Install with `pip install .` from pyproject.toml

## Key Constants

Located in `src/main.py`:
- `CONVERSATION_DELAY_SECONDS = 0.5`
- `LARGE_CONVERSATION_THRESHOLD = 10000`
- `CHUNK_DATE_RANGE_DAYS = 30`
- `CHUNK_MESSAGE_THRESHOLD = 10000`

Located in `src/slack_client.py`:
- `DEFAULT_PAGE_SIZE = 200`
- `MAX_RETRIES = 3`
- `SHARE_RATE_LIMIT_INTERVAL = 10`

Located in `src/google_drive.py`:
- `GOOGLE_DRIVE_MAX_FOLDER_NAME_LENGTH = 255`
- `SECURE_FILE_PERMISSIONS = 0o600`

## Bulk Export Logic

The bulk export feature (`--bulk-export`) automatically chunks large exports:

1. **Chunking triggers** when:
   - Message count > `CHUNK_MESSAGE_THRESHOLD` (10,000), OR
   - Date range > `CHUNK_DATE_RANGE_DAYS` (30 days)

2. **Chunking method**: Monthly chunks using `split_messages_by_month()`

3. **File naming**: `{channel_name}_history_{YYYY-MM}_{timestamp}.txt`

4. **Metadata**: Each chunk includes date range and chunk number in header

## Common Pitfalls

1. **Forgetting rate limits**: Always use rate limiting before API calls
2. **Not sanitizing paths**: Use `sanitize_path_for_logging()` in error messages
3. **Token permissions**: Token files must be 600, check in code
4. **State management**: Don't create local state files - use Drive metadata
5. **Error handling**: Distinguish between `None` (API error) and `[]` (no messages)

## When Making Changes

1. **Run formatters first**: `black src/ tests/` and `isort src/ tests/`
2. **Run tests**: `pytest` - ensure all 156 tests pass
3. **Check linting**: `pylint src/` - fix any critical issues
4. **Update docs**: If behavior changes, update README.md
5. **Update requirements**: If adding dependencies, update `pyproject.toml` and regenerate `requirements.txt`

## Questions to Consider

Before making changes, ask:
- Does this maintain backward compatibility?
- Is this stateless (no local state files)?
- Are API calls rate-limited?
- Are errors handled gracefully?
- Are sensitive data sanitized in logs?
- Are tests updated?
- Is documentation updated?

## Getting Help

- Check existing tests for examples of how to use functions
- Review `ReadMe.md` for user-facing documentation
- Look at similar functions in the codebase for patterns
- Check `CODE_REVIEW.md` for known issues and improvements

---

**Last Updated**: 2025-01-06  
**Project Version**: 1.0.0  
**Python Version**: 3.9+
