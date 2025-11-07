"""
Unit tests for main.py functions.

Tests cover preprocessing and conversation display name logic.
"""
import pytest
from unittest.mock import Mock, patch
from datetime import datetime, timezone
from src.main import (
    preprocess_history, 
    get_conversation_display_name,
    should_chunk_export,
    split_messages_by_month,
    estimate_file_size
)
from src.slack_client import SlackClient


class TestPreprocessHistory:
    """Tests for preprocess_history function."""
    
    def test_preprocess_simple_messages(self):
        slack_client = Mock(spec=SlackClient)
        slack_client.get_user_info.return_value = {
            "slackId": "U123",
            "displayName": "Test User",
            "email": "test@example.com"
        }
        
        history = [
            {
                "ts": "1234567890.123",
                "user": "U123",
                "text": "Hello world"
            },
            {
                "ts": "1234567891.123",
                "user": "U123",
                "text": "Second message"
            }
        ]
        
        result = preprocess_history(history, slack_client)
        
        assert "Hello world" in result
        assert "Second message" in result
        assert "Test User" in result
        assert "UTC" in result  # Timestamp formatting
    
    def test_preprocess_with_threads(self):
        slack_client = Mock(spec=SlackClient)
        slack_client.get_user_info.return_value = {
            "slackId": "U123",
            "displayName": "Test User",
            "email": "test@example.com"
        }
        
        history = [
            {
                "ts": "1234567890.123",
                "thread_ts": "1234567890.123",
                "user": "U123",
                "text": "Parent message"
            },
            {
                "ts": "1234567891.123",
                "thread_ts": "1234567890.123",
                "user": "U123",
                "text": "Reply message"
            }
        ]
        
        result = preprocess_history(history, slack_client)
        
        assert "Parent message" in result
        assert "Reply message" in result
        assert ">" in result  # Replies are indented
    
    def test_preprocess_with_files(self):
        slack_client = Mock(spec=SlackClient)
        slack_client.get_user_info.return_value = {
            "slackId": "U123",
            "displayName": "Test User",
            "email": "test@example.com"
        }
        
        history = [
            {
                "ts": "1234567890.123",
                "user": "U123",
                "text": "Check this out",
                "files": [{"id": "F123"}]
            }
        ]
        
        result = preprocess_history(history, slack_client)
        
        assert "Check this out" in result
        assert "[File attached]" in result
    
    def test_preprocess_with_people_cache(self):
        slack_client = Mock(spec=SlackClient)
        people_cache = {"U123": "Cached User"}
        
        history = [
            {
                "ts": "1234567890.123",
                "user": "U123",
                "text": "Message"
            }
        ]
        
        result = preprocess_history(history, slack_client, people_cache)
        
        assert "Cached User" in result
        # Verify API was not called
        slack_client.get_user_info.assert_not_called()
    
    def test_preprocess_skips_messages_without_text_or_files(self):
        slack_client = Mock(spec=SlackClient)
        
        history = [
            {
                "ts": "1234567890.123",
                "user": "U123",
                "text": "",
                "files": None
            }
        ]
        
        result = preprocess_history(history, slack_client)
        
        # Empty result or no user mention
        assert "U123" not in result or result.strip() == ""


class TestGetConversationDisplayName:
    """Tests for get_conversation_display_name function."""
    
    def test_channel_with_display_name(self):
        slack_client = Mock(spec=SlackClient)
        channel_info = {
            "id": "C123456",
            "displayName": "general"
        }
        
        result = get_conversation_display_name(channel_info, slack_client)
        assert result == "general"
    
    def test_channel_with_name(self):
        slack_client = Mock(spec=SlackClient)
        channel_info = {
            "id": "C123456",
            "name": "random"
        }
        
        result = get_conversation_display_name(channel_info, slack_client)
        assert result == "random"
    
    def test_dm_conversation(self):
        slack_client = Mock(spec=SlackClient)
        slack_client.get_user_info.return_value = {
            "slackId": "U123",
            "displayName": "John Doe",
            "email": "john@example.com"
        }
        
        channel_info = {
            "id": "D123456",
            "is_im": True,
            "user": "U123"
        }
        
        result = get_conversation_display_name(channel_info, slack_client)
        assert result == "John Doe"
    
    def test_dm_without_user_info(self):
        slack_client = Mock(spec=SlackClient)
        slack_client.get_user_info.return_value = None
        
        channel_info = {
            "id": "D123456",
            "is_im": True,
            "user": "U123"
        }
        
        result = get_conversation_display_name(channel_info, slack_client)
        assert result.startswith("dm_")
        assert "123456"[:8] in result
    
    def test_group_dm(self):
        slack_client = Mock(spec=SlackClient)
        slack_client.get_user_info.side_effect = [
            {"slackId": "U123", "displayName": "Alice", "email": "alice@example.com"},
            {"slackId": "U456", "displayName": "Bob", "email": "bob@example.com"}
        ]
        
        channel_info = {
            "id": "G123456",
            "is_mpim": True,
            "members": ["U123", "U456"]
        }
        
        result = get_conversation_display_name(channel_info, slack_client)
        # Should contain both names, sorted
        assert "Alice" in result
        assert "Bob" in result
        assert "," in result
    
    def test_group_dm_no_members(self):
        slack_client = Mock(spec=SlackClient)
        # Mock get_channel_members to return empty list (simulating no members found)
        slack_client.get_channel_members.return_value = []
        
        channel_info = {
            "id": "G123456",
            "is_mpim": True,
            "members": []
        }
        
        result = get_conversation_display_name(channel_info, slack_client)
        assert result.startswith("group_dm_")
        # Verify that get_channel_members was called when members list is empty
        slack_client.get_channel_members.assert_called_once_with("G123456")
    
    def test_group_dm_members_fetched_dynamically(self):
        """Test that members are fetched dynamically when missing from channel_info."""
        slack_client = Mock(spec=SlackClient)
        # Mock get_channel_members to return member IDs
        slack_client.get_channel_members.return_value = ["U123", "U456"]
        # Mock get_user_info to return user details
        slack_client.get_user_info.side_effect = [
            {"slackId": "U123", "displayName": "Alice", "email": "alice@example.com"},
            {"slackId": "U456", "displayName": "Bob", "email": "bob@example.com"}
        ]
        
        channel_info = {
            "id": "G123456",
            "is_mpim": True,
            # members field is missing or empty
        }
        
        result = get_conversation_display_name(channel_info, slack_client)
        # Should contain both names, sorted
        assert "Alice" in result
        assert "Bob" in result
        assert "," in result
        # Verify that get_channel_members was called
        slack_client.get_channel_members.assert_called_once_with("G123456")
    
    def test_missing_id(self):
        slack_client = Mock(spec=SlackClient)
        channel_info = {}
        
        result = get_conversation_display_name(channel_info, slack_client)
        assert result == "unknown_conversation"
    
    def test_channel_fallback_to_id(self):
        slack_client = Mock(spec=SlackClient)
        channel_info = {
            "id": "C123456"
        }
        
        result = get_conversation_display_name(channel_info, slack_client)
        assert result == "C123456"
    
    def test_empty_name_fallback(self):
        slack_client = Mock(spec=SlackClient)
        channel_info = {
            "id": "C123456",
            "name": ""
        }
        
        result = get_conversation_display_name(channel_info, slack_client)
        # When name is empty, it falls back to channel ID
        assert result == "C123456"


class TestShouldChunkExport:
    """Tests for should_chunk_export function."""
    
    def test_no_chunking_when_bulk_export_disabled(self):
        """Should not chunk when bulk export is disabled."""
        history = [{"ts": "1234567890.123"} for _ in range(20000)]
        result = should_chunk_export(history, None, None, bulk_export=False)
        assert result is False
    
    def test_chunking_by_message_count(self):
        """Should chunk when message count exceeds threshold."""
        # Create history with more than 10,000 messages
        history = [{"ts": "1234567890.123"} for _ in range(15000)]
        result = should_chunk_export(history, None, None, bulk_export=True)
        assert result is True
    
    def test_chunking_by_date_range_with_timestamps(self):
        """Should chunk when date range exceeds threshold."""
        # Create timestamps 60 days apart
        oldest_ts = "1609459200"  # 2021-01-01
        latest_ts = "1612137600"  # 2021-02-01 (31 days later)
        history = [{"ts": "1234567890.123"} for _ in range(100)]
        result = should_chunk_export(history, oldest_ts, latest_ts, bulk_export=True)
        assert result is True
    
    def test_no_chunking_when_below_thresholds(self):
        """Should not chunk when both thresholds are below limits."""
        oldest_ts = "1609459200"  # 2021-01-01
        latest_ts = "1609545600"  # 2021-01-02 (1 day later)
        history = [{"ts": "1234567890.123"} for _ in range(100)]
        result = should_chunk_export(history, oldest_ts, latest_ts, bulk_export=True)
        assert result is False
    
    def test_chunking_by_date_range_from_messages(self):
        """Should chunk when date range calculated from messages exceeds threshold."""
        # Create messages spanning 60 days
        base_ts = 1609459200  # 2021-01-01
        history = []
        for i in range(100):
            ts = base_ts + (i * 86400)  # One day per message
            history.append({"ts": str(ts)})
        result = should_chunk_export(history, None, None, bulk_export=True)
        assert result is True
    
    def test_no_chunking_for_empty_history(self):
        """Should not chunk empty history."""
        result = should_chunk_export([], None, None, bulk_export=True)
        assert result is False
    
    def test_no_chunking_for_single_message(self):
        """Should not chunk single message."""
        history = [{"ts": "1234567890.123"}]
        result = should_chunk_export(history, None, None, bulk_export=True)
        assert result is False


class TestSplitMessagesByMonth:
    """Tests for split_messages_by_month function."""
    
    def test_empty_history(self):
        """Should return empty list for empty history."""
        result = split_messages_by_month([])
        assert result == []
    
    def test_single_message(self):
        """Should handle single message."""
        dt = datetime(2023, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        ts = str(dt.timestamp())
        history = [{"ts": ts, "text": "Hello"}]
        
        result = split_messages_by_month(history)
        assert len(result) == 1
        start_date, end_date, messages = result[0]
        assert start_date.year == 2023
        assert start_date.month == 1
        assert len(messages) == 1
    
    def test_messages_in_same_month(self):
        """Should group messages from same month together."""
        base_dt = datetime(2023, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        history = []
        for i in range(5):
            dt = base_dt.replace(day=15 + i)
            ts = str(dt.timestamp())
            history.append({"ts": ts, "text": f"Message {i}"})
        
        result = split_messages_by_month(history)
        assert len(result) == 1
        start_date, end_date, messages = result[0]
        assert start_date.month == 1
        assert len(messages) == 5
    
    def test_messages_across_multiple_months(self):
        """Should split messages across multiple months."""
        history = []
        # January messages
        for day in [15, 20, 25]:
            dt = datetime(2023, 1, day, 12, 0, 0, tzinfo=timezone.utc)
            history.append({"ts": str(dt.timestamp()), "text": f"Jan {day}"})
        
        # February messages
        for day in [5, 10, 15]:
            dt = datetime(2023, 2, day, 12, 0, 0, tzinfo=timezone.utc)
            history.append({"ts": str(dt.timestamp()), "text": f"Feb {day}"})
        
        # March messages
        for day in [1, 5]:
            dt = datetime(2023, 3, day, 12, 0, 0, tzinfo=timezone.utc)
            history.append({"ts": str(dt.timestamp()), "text": f"Mar {day}"})
        
        result = split_messages_by_month(history)
        assert len(result) == 3
        
        # Check January chunk
        start_date, end_date, messages = result[0]
        assert start_date.year == 2023
        assert start_date.month == 1
        assert len(messages) == 3
        
        # Check February chunk
        start_date, end_date, messages = result[1]
        assert start_date.month == 2
        assert len(messages) == 3
        
        # Check March chunk
        start_date, end_date, messages = result[2]
        assert start_date.month == 3
        assert len(messages) == 2
    
    def test_messages_across_year_boundary(self):
        """Should handle messages across year boundary."""
        history = []
        # December 2022
        dt = datetime(2022, 12, 15, 12, 0, 0, tzinfo=timezone.utc)
        history.append({"ts": str(dt.timestamp()), "text": "Dec 2022"})
        
        # January 2023
        dt = datetime(2023, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        history.append({"ts": str(dt.timestamp()), "text": "Jan 2023"})
        
        result = split_messages_by_month(history)
        assert len(result) == 2
        
        # Check December chunk
        start_date, end_date, messages = result[0]
        assert start_date.year == 2022
        assert start_date.month == 12
        assert len(messages) == 1
        
        # Check January chunk
        start_date, end_date, messages = result[1]
        assert start_date.year == 2023
        assert start_date.month == 1
        assert len(messages) == 1
    
    def test_end_date_is_end_of_month(self):
        """Should set end date to end of month."""
        dt = datetime(2023, 2, 15, 12, 0, 0, tzinfo=timezone.utc)
        history = [{"ts": str(dt.timestamp()), "text": "Message"}]
        
        result = split_messages_by_month(history)
        start_date, end_date, messages = result[0]
        
        # February 2023 has 28 days
        assert end_date.year == 2023
        assert end_date.month == 2
        assert end_date.day == 28
        assert end_date.hour == 23
        assert end_date.minute == 59
        assert end_date.second == 59
    
    def test_messages_sorted_by_timestamp(self):
        """Should handle messages that are already sorted."""
        history = []
        for month in [1, 2, 3]:
            dt = datetime(2023, month, 15, 12, 0, 0, tzinfo=timezone.utc)
            history.append({"ts": str(dt.timestamp()), "text": f"Month {month}"})
        
        result = split_messages_by_month(history)
        assert len(result) == 3
        
        # Verify chronological order
        for i in range(len(result) - 1):
            current_end = result[i][1]
            next_start = result[i + 1][0]
            assert current_end < next_start or (current_end.year == next_start.year and 
                                                current_end.month < next_start.month)


class TestEstimateFileSize:
    """Tests for estimate_file_size function."""
    
    def test_empty_string(self):
        """Should return 0 for empty string."""
        result = estimate_file_size("")
        assert result == 0
    
    def test_simple_string(self):
        """Should estimate size correctly for simple string."""
        text = "Hello, World!"
        result = estimate_file_size(text)
        # UTF-8 encoding: each ASCII character is 1 byte
        assert result == len(text.encode('utf-8'))
        assert result == 13
    
    def test_unicode_string(self):
        """Should handle Unicode characters correctly."""
        # Use euro sign which takes 3 bytes in UTF-8
        text = "Price: \u20ac100"  # ? symbol
        result = estimate_file_size(text)
        expected = len(text.encode('utf-8'))
        assert result == expected
        # UTF-8 encoding: "Price: " = 7 bytes, "?" = 3 bytes, "100" = 3 bytes = 13 bytes total
        # Character count: 11 characters
        assert result == 13  # Verify exact byte count
        assert result > len(text)  # Byte count should be larger than character count for multi-byte Unicode
    
    def test_multiline_string(self):
        """Should handle multiline strings."""
        text = "Line 1\nLine 2\nLine 3"
        result = estimate_file_size(text)
        assert result == len(text.encode('utf-8'))
    
    def test_large_string(self):
        """Should handle large strings."""
        text = "A" * 10000
        result = estimate_file_size(text)
        assert result == 10000  # Each 'A' is 1 byte in UTF-8
