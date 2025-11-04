# Unit Tests

This directory contains comprehensive unit tests for the slackfeeder project.

## Test Coverage

### `test_utils.py`
Tests for utility functions that are pure functions (no external dependencies):
- `sanitize_filename()` - 8 test cases
- `sanitize_folder_name()` - 6 test cases
- `validate_email()` - 5 test cases
- `validate_channel_id()` - 2 test cases (valid/invalid)
- `validate_channels_json()` - 4 test cases
- `validate_people_json()` - 6 test cases
- `format_timestamp()` - 3 test cases
- `convert_date_to_timestamp()` - 6 test cases
- `load_json_file()` - 3 test cases
- `save_json_file()` - 3 test cases

**Total: ~46 test cases**

### `test_google_drive.py`
Tests for Google Drive client using mocked API calls:
- `_escape_drive_query_string()` - 6 test cases
- `_validate_folder_id()` - 2 test cases
- `find_folder()` - 4 test cases
- `create_folder()` - 4 test cases
- `upload_file()` - 3 test cases
- `share_folder()` - 4 test cases

**Total: ~23 test cases**

### `test_slack_client.py`
Tests for Slack client using mocked API calls:
- `SlackClient.__init__()` - 2 test cases
- `get_user_info()` - 4 test cases
- `get_channel_members()` - 3 test cases
- `fetch_channel_history()` - 6 test cases

**Total: ~15 test cases**

### `test_main.py`
Tests for main processing functions:
- `preprocess_history()` - 5 test cases
- `get_conversation_display_name()` - 9 test cases

**Total: ~14 test cases**

## Running Tests

### Install Dependencies
```bash
pip install -r requirements.txt
```

### Run All Tests
```bash
pytest
```

### Run Specific Test File
```bash
pytest tests/test_utils.py
pytest tests/test_google_drive.py
pytest tests/test_slack_client.py
pytest tests/test_main.py
```

### Run with Verbose Output
```bash
pytest -v
```

### Run with Coverage Report
```bash
pip install pytest-cov
pytest --cov=src --cov-report=html
```

### Run Specific Test
```bash
pytest tests/test_utils.py::TestValidateEmail::test_valid_emails
```

## Test Design Principles

1. **Isolation**: Each test is independent and doesn't rely on other tests
2. **Mocking**: External API calls (Slack, Google Drive) are mocked to avoid requiring credentials
3. **Edge Cases**: Tests cover normal cases, edge cases, and error conditions
4. **Readability**: Test names clearly describe what is being tested
5. **Maintainability**: Tests use fixtures and helper functions to reduce duplication

## Adding New Tests

When adding new functionality:

1. Add tests alongside the code changes
2. Follow the existing naming convention: `test_<function_name>` or `Test<ClassName>`
3. Use descriptive test names that explain what is being tested
4. Mock external dependencies (APIs, file system operations)
5. Test both success and failure cases
6. Test edge cases (empty strings, None, boundary values)

## Test Fixtures

Shared fixtures are defined in `conftest.py`:
- `temp_dir` - Temporary directory for test files
- `temp_file` - Temporary file for testing
- `sample_channels_json` - Sample channels.json data
- `sample_people_json` - Sample people.json data

## Coverage Goals

- **Current**: ~98 test cases covering critical functions
- **Target**: Increase coverage for error handling paths and edge cases
- **Priority**: Focus on validation functions and API error handling

## Notes

- Tests use `unittest.mock` for mocking external dependencies
- Tests don't require actual Slack or Google Drive credentials
- All tests run in isolation without side effects
- Temporary files created during tests are automatically cleaned up
