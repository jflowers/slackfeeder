"""
Tests for browser-based Slack DM scraper functionality.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.browser_response_processor import BrowserResponseProcessor
from src.browser_scraper import (
    extract_messages_from_response,
    find_conversations_history_requests,
    get_response_metadata,
)


class TestExtractMessagesFromResponse:
    """Test message extraction from API responses."""

    def test_extract_valid_response(self):
        """Test extracting messages from a valid API response."""
        response_data = {
            "ok": True,
            "messages": [
                {"ts": "1729263032.513419", "user": "U123", "text": "Hello"},
                {"ts": "1729263033.513419", "user": "U456", "text": "Hi there"},
            ],
        }
        messages = extract_messages_from_response(response_data)
        assert len(messages) == 2
        assert messages[0]["text"] == "Hello"
        assert messages[1]["text"] == "Hi there"

    def test_extract_empty_messages(self):
        """Test extracting from response with no messages."""
        response_data = {"ok": True, "messages": []}
        messages = extract_messages_from_response(response_data)
        assert len(messages) == 0

    def test_extract_failed_response(self):
        """Test extracting from failed API response."""
        response_data = {"ok": False, "messages": []}
        messages = extract_messages_from_response(response_data)
        assert len(messages) == 0

    def test_extract_invalid_response(self):
        """Test extracting from invalid response structure."""
        response_data = {"not_ok": True}
        messages = extract_messages_from_response(response_data)
        assert len(messages) == 0

    def test_extract_non_dict_response(self):
        """Test extracting from non-dictionary response."""
        messages = extract_messages_from_response([])
        assert len(messages) == 0


class TestGetResponseMetadata:
    """Test metadata extraction from API responses."""

    def test_get_metadata_complete(self):
        """Test extracting complete metadata."""
        response_data = {
            "ok": True,
            "has_more": True,
            "oldest": "1729263032.513419",
            "latest": "1729263033.513419",
            "messages": [{"ts": "1729263032.513419", "text": "Hello"}],
        }
        metadata = get_response_metadata(response_data)
        assert metadata["has_more"] is True
        assert metadata["oldest"] == "1729263032.513419"
        assert metadata["latest"] == "1729263033.513419"
        assert metadata["message_count"] == 1

    def test_get_metadata_minimal(self):
        """Test extracting metadata from minimal response."""
        response_data = {"ok": True, "messages": []}
        metadata = get_response_metadata(response_data)
        assert metadata["has_more"] is False
        assert metadata["oldest"] is None
        assert metadata["latest"] is None
        assert metadata["message_count"] == 0


class TestFindConversationsHistoryRequests:
    """Test filtering network requests."""

    def test_find_history_requests(self):
        """Test finding conversations.history requests."""
        network_requests = [
            {"url": "https://slack.com/api/conversations.history", "id": 1},
            {"url": "https://slack.com/api/users.list", "id": 2},
            {"url": "https://slack.com/api/conversations.history?channel=D123", "id": 3},
        ]
        history_requests = find_conversations_history_requests(network_requests)
        assert len(history_requests) == 2
        assert history_requests[0]["id"] == 1
        assert history_requests[1]["id"] == 3

    def test_find_no_history_requests(self):
        """Test finding no history requests."""
        network_requests = [
            {"url": "https://slack.com/api/users.list", "id": 1},
            {"url": "https://slack.com/api/channels.list", "id": 2},
        ]
        history_requests = find_conversations_history_requests(network_requests)
        assert len(history_requests) == 0

    def test_find_empty_requests(self):
        """Test finding in empty request list."""
        history_requests = find_conversations_history_requests([])
        assert len(history_requests) == 0


class TestBrowserResponseProcessor:
    """Test browser response processor."""

    def test_discover_user_ids(self):
        """Test discovering user IDs from messages."""
        processor = BrowserResponseProcessor()
        messages = [
            {"user": "U123", "text": "Hello"},
            {"user": "U456", "text": "Hi"},
            {"user": "U123", "text": "Again"},
        ]
        user_map = processor.discover_user_ids(messages)
        assert "U123" in user_map
        assert "U456" in user_map
        assert len(user_map) == 2

    def test_discover_user_ids_with_existing_map(self):
        """Test discovering user IDs with existing map."""
        processor = BrowserResponseProcessor(user_map={"U123": "Alice"})
        messages = [
            {"user": "U123", "text": "Hello"},
            {"user": "U456", "text": "Hi"},
        ]
        user_map = processor.discover_user_ids(messages)
        assert user_map["U123"] == "Alice"  # Preserved from existing map
        assert user_map["U456"] == "U456"  # New ID uses ID as name

    def test_parse_timestamp(self):
        """Test parsing Slack timestamp."""
        processor = BrowserResponseProcessor()
        dt = processor.parse_timestamp("1729263032.513419")
        assert dt.year == 2024
        assert dt.month == 10

    def test_format_message_text_simple(self):
        """Test formatting simple message text."""
        processor = BrowserResponseProcessor()
        message = {"text": "Hello world"}
        text = processor.format_message_text(message)
        assert text == "Hello world"

    def test_format_message_text_from_blocks(self):
        """Test extracting text from blocks when text field is empty."""
        processor = BrowserResponseProcessor()
        message = {
            "text": "",
            "blocks": [
                {
                    "elements": [
                        {
                            "type": "rich_text_section",
                            "elements": [
                                {"type": "text", "text": "Hello "},
                                {"type": "emoji", "name": "wave"},
                                {"type": "text", "text": " world"},
                            ],
                        }
                    ]
                }
            ],
        }
        text = processor.format_message_text(message)
        assert "Hello" in text
        assert "world" in text

    def test_group_messages_by_date(self):
        """Test grouping messages by date."""
        processor = BrowserResponseProcessor()
        messages = [
            {"ts": "1729263032.513419", "text": "Message 1"},  # Oct 18, 2024
            {"ts": "1729176632.513419", "text": "Message 2"},  # Oct 17, 2024
            {"ts": "1729263033.513419", "text": "Message 3"},  # Oct 18, 2024
        ]
        grouped = processor.group_messages_by_date(messages)
        assert "2024-10-18" in grouped
        assert "2024-10-17" in grouped
        assert len(grouped["2024-10-18"]) == 2
        assert len(grouped["2024-10-17"]) == 1

    def test_process_responses(self):
        """Test processing response files."""
        processor = BrowserResponseProcessor()

        # Create temporary response files
        with tempfile.TemporaryDirectory() as tmpdir:
            response_dir = Path(tmpdir) / "responses"
            output_dir = Path(tmpdir) / "output"
            response_dir.mkdir()

            # Create a sample response file
            response_data = {
                "ok": True,
                "messages": [
                    {
                        "ts": "1729263032.513419",
                        "user": "U123",
                        "text": "Hello",
                    },
                    {
                        "ts": "1729263033.513419",
                        "user": "U456",
                        "text": "Hi there",
                    },
                ],
            }
            response_file = response_dir / "response_0.json"
            with open(response_file, "w") as f:
                json.dump(response_data, f)

            # Process responses
            total_messages, date_counts = processor.process_responses(
                [response_file], output_dir, "TestDM"
            )

            assert total_messages == 2
            assert len(date_counts) == 1
            assert "2024-10-18" in date_counts

            # Check output file was created
            output_file = output_dir / "2024-10-18-TestDM.txt"
            assert output_file.exists()
            content = output_file.read_text()
            assert "Hello" in content
            assert "Hi there" in content

    def test_process_responses_deduplication(self):
        """Test that duplicate messages are deduplicated."""
        processor = BrowserResponseProcessor()

        with tempfile.TemporaryDirectory() as tmpdir:
            response_dir = Path(tmpdir) / "responses"
            output_dir = Path(tmpdir) / "output"
            response_dir.mkdir()

            # Create two response files with overlapping messages
            response1 = {
                "ok": True,
                "messages": [
                    {"ts": "1729263032.513419", "user": "U123", "text": "Message 1"},
                    {"ts": "1729263033.513419", "user": "U123", "text": "Message 2"},
                ],
            }
            response2 = {
                "ok": True,
                "messages": [
                    {"ts": "1729263033.513419", "user": "U123", "text": "Message 2"},  # Duplicate
                    {"ts": "1729263034.513419", "user": "U123", "text": "Message 3"},
                ],
            }

            file1 = response_dir / "response_0.json"
            file2 = response_dir / "response_1.json"
            with open(file1, "w") as f:
                json.dump(response1, f)
            with open(file2, "w") as f:
                json.dump(response2, f)

            # Process responses
            total_messages, date_counts = processor.process_responses(
                [file1, file2], output_dir, "TestDM"
            )

            # Should have 3 unique messages, not 4
            assert total_messages == 3

    def test_format_message_for_export(self):
        """Test formatting message for export."""
        # Use valid Slack user ID format (U + 8+ chars)
        user_id = "U1234567890"
        processor = BrowserResponseProcessor(user_map={user_id: "Alice"})
        message = {
            "ts": "1729263032.513419",
            "user": user_id,
            "text": "Hello world",
        }
        formatted = processor.format_message_for_export(message, {user_id: "Alice"})
        assert "Alice" in formatted
        assert "Hello world" in formatted

    def test_format_message_with_reactions(self):
        """Test formatting message with reactions."""
        processor = BrowserResponseProcessor()
        message = {
            "ts": "1729263032.513419",
            "user": "U123",
            "text": "Hello",
            "reactions": [{"name": "thumbsup", "count": 2}],
        }
        formatted = processor.format_message_for_export(message, {"U123": "User"})
        assert "Reactions:" in formatted
        assert "thumbsup" in formatted
        assert "(2)" in formatted

    def test_format_message_with_files(self):
        """Test formatting message with files."""
        processor = BrowserResponseProcessor()
        message = {
            "ts": "1729263032.513419",
            "user": "U123",
            "text": "Check this",
            "files": [{"name": "document.pdf"}],
        }
        formatted = processor.format_message_for_export(message, {"U123": "User"})
        assert "[File:" in formatted
        assert "document.pdf" in formatted

    def test_preprocess_messages_for_google_doc(self):
        """Test preprocessing messages for Google Doc format."""
        # Use valid Slack user ID format (U + 8+ chars)
        user_id_1 = "U1234567890"
        user_id_2 = "U0987654321"
        processor = BrowserResponseProcessor(user_map={user_id_1: "Alice", user_id_2: "Bob"})
        messages = [
            {
                "ts": "1729263032.513419",
                "user": user_id_1,
                "text": "Hello world",
                "thread_ts": "1729263032.513419",
            },
            {
                "ts": "1729263033.513419",
                "user": user_id_2,
                "text": "Hi there",
                "thread_ts": "1729263032.513419",  # Reply in same thread
            },
        ]
        formatted = processor.preprocess_messages_for_google_doc(messages, processor.user_map)
        assert "Alice" in formatted
        assert "Bob" in formatted
        assert "Hello world" in formatted
        assert "Hi there" in formatted
        # Should have thread formatting with indentation
        assert "    >" in formatted or ">" in formatted

    def test_preprocess_messages_with_files(self):
        """Test preprocessing messages with files."""
        processor = BrowserResponseProcessor(user_map={"U123": "Alice"})
        messages = [
            {
                "ts": "1729263032.513419",
                "user": "U123",
                "text": "",
                "files": [{"name": "document.pdf"}],
                "thread_ts": "1729263032.513419",
            }
        ]
        formatted = processor.preprocess_messages_for_google_doc(messages, processor.user_map)
        assert "[File attached]" in formatted

    def test_preprocess_messages_empty(self):
        """Test preprocessing empty message list."""
        processor = BrowserResponseProcessor()
        formatted = processor.preprocess_messages_for_google_doc([], {})
        assert formatted == ""

    def test_preprocess_messages_no_text_no_files(self):
        """Test preprocessing messages with no text and no files (should be skipped)."""
        processor = BrowserResponseProcessor()
        messages = [
            {
                "ts": "1729263032.513419",
                "user": "U123",
                "text": "",
                "thread_ts": "1729263032.513419",
            }
        ]
        formatted = processor.preprocess_messages_for_google_doc(messages, {})
        # Should be empty or minimal since message has no content
        assert len(formatted.strip()) == 0 or "U123" not in formatted

    def test_process_responses_for_google_drive(self):
        """Test processing responses for Google Drive (groups by YYYYMMDD format)."""
        processor = BrowserResponseProcessor()

        with tempfile.TemporaryDirectory() as tmpdir:
            response_dir = Path(tmpdir) / "responses"
            response_dir.mkdir()

            # Create response file with messages from different dates
            response_data = {
                "ok": True,
                "messages": [
                    {
                        "ts": "1729263032.513419",  # Oct 18, 2024
                        "user": "U123",
                        "text": "Message 1",
                    },
                    {
                        "ts": "1729176632.513419",  # Oct 17, 2024
                        "user": "U456",
                        "text": "Message 2",
                    },
                    {
                        "ts": "1729263033.513419",  # Oct 18, 2024
                        "user": "U123",
                        "text": "Message 3",
                    },
                ],
            }
            response_file = response_dir / "response_0.json"
            with open(response_file, "w") as f:
                json.dump(response_data, f)

            # Process for Google Drive
            daily_groups, user_map = processor.process_responses_for_google_drive(
                [response_file], "TestDM"
            )

            # Should group by YYYYMMDD format (not YYYY-MM-DD)
            assert "20241018" in daily_groups
            assert "20241017" in daily_groups
            assert len(daily_groups["20241018"]) == 2
            assert len(daily_groups["20241017"]) == 1
            assert "U123" in user_map
            assert "U456" in user_map

    def test_process_responses_for_google_drive_deduplication(self):
        """Test that process_responses_for_google_drive deduplicates messages."""
        processor = BrowserResponseProcessor()

        with tempfile.TemporaryDirectory() as tmpdir:
            response_dir = Path(tmpdir) / "responses"
            response_dir.mkdir()

            # Create two response files with overlapping messages
            response1 = {
                "ok": True,
                "messages": [
                    {"ts": "1729263032.513419", "user": "U123", "text": "Message 1"},
                    {"ts": "1729263033.513419", "user": "U123", "text": "Message 2"},
                ],
            }
            response2 = {
                "ok": True,
                "messages": [
                    {"ts": "1729263033.513419", "user": "U123", "text": "Message 2"},  # Duplicate
                    {"ts": "1729263034.513419", "user": "U123", "text": "Message 3"},
                ],
            }

            file1 = response_dir / "response_0.json"
            file2 = response_dir / "response_1.json"
            with open(file1, "w") as f:
                json.dump(response1, f)
            with open(file2, "w") as f:
                json.dump(response2, f)

            # Process for Google Drive
            daily_groups, user_map = processor.process_responses_for_google_drive(
                [file1, file2], "TestDM"
            )

            # Should have 3 unique messages, not 4
            total_messages = sum(len(msgs) for msgs in daily_groups.values())
            assert total_messages == 3

    def test_process_responses_for_google_drive_empty(self):
        """Test processing empty response files."""
        processor = BrowserResponseProcessor()

        with tempfile.TemporaryDirectory() as tmpdir:
            response_dir = Path(tmpdir) / "responses"
            response_dir.mkdir()

            # Create empty response file
            response_data = {"ok": True, "messages": []}
            response_file = response_dir / "response_0.json"
            with open(response_file, "w") as f:
                json.dump(response_data, f)

            daily_groups, user_map = processor.process_responses_for_google_drive(
                [response_file], "TestDM"
            )

            assert len(daily_groups) == 0
            assert len(user_map) == 0

    def test_process_responses_for_google_drive_nonexistent_file(self):
        """Test processing with nonexistent file."""
        processor = BrowserResponseProcessor()
        nonexistent_file = Path("/nonexistent/response.json")

        daily_groups, user_map = processor.process_responses_for_google_drive(
            [nonexistent_file], "TestDM"
        )

        assert len(daily_groups) == 0
        assert len(user_map) == 0

    def test_format_message_for_google_doc(self):
        """Test formatting message for Google Doc (matches main export format)."""
        # Use valid Slack user ID format (U + 8+ chars)
        user_id = "U1234567890"
        processor = BrowserResponseProcessor(user_map={user_id: "Alice"})
        message = {
            "ts": "1729263032.513419",
            "user": user_id,
            "text": "Hello world",
        }
        formatted, ts = processor.format_message_for_google_doc(message, {user_id: "Alice"})
        assert "Alice" in formatted
        assert "Hello world" in formatted
        # Should have timestamp format like [YYYY-MM-DD HH:MM:SS UTC]
        assert "[" in formatted and "]" in formatted
        assert ts == "1729263032.513419"

    def test_format_message_for_google_doc_with_files(self):
        """Test formatting message with files for Google Doc."""
        processor = BrowserResponseProcessor()
        message = {
            "ts": "1729263032.513419",
            "user": "U123",
            "text": "Check this",
            "files": [{"name": "document.pdf"}],
        }
        formatted, _ = processor.format_message_for_google_doc(message, {"U123": "User"})
        assert "[File attached]" in formatted

    def test_format_message_for_google_doc_no_text_with_files(self):
        """Test formatting message with no text but files."""
        processor = BrowserResponseProcessor()
        message = {
            "ts": "1729263032.513419",
            "user": "U123",
            "text": "",
            "files": [{"name": "document.pdf"}],
        }
        formatted, _ = processor.format_message_for_google_doc(message, {"U123": "User"})
        assert "[File attached]" in formatted
