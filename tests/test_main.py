"""
Unit tests for main.py functions.

Tests cover preprocessing and conversation display name logic.
"""

import json
from datetime import datetime, timezone
from unittest.mock import Mock, patch

import pytest

from src.main import (
    _get_conversation_members,
    _initialize_stats,
    _should_share_with_member,
    estimate_file_size,
    filter_messages_by_date_range,
    find_conversation_in_config,
    get_conversation_display_name,
    get_oldest_timestamp_for_export,
    group_messages_by_date,
    load_browser_export_config,
    preprocess_history,
    replace_user_ids_in_text,
    should_chunk_export,
    split_messages_by_month,
)
from src.slack_client import SlackClient


class TestPreprocessHistory:
    """Tests for preprocess_history function."""

    def test_preprocess_simple_messages(self):
        slack_client = Mock(spec=SlackClient)
        slack_client.get_user_info.return_value = {
            "slackId": "U123",
            "displayName": "Test User",
            "email": "test@example.com",
        }

        history = [
            {"ts": "1234567890.123", "user": "U123", "text": "Hello world"},
            {"ts": "1234567891.123", "user": "U123", "text": "Second message"},
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
            "email": "test@example.com",
        }

        history = [
            {
                "ts": "1234567890.123",
                "thread_ts": "1234567890.123",
                "user": "U123",
                "text": "Parent message",
            },
            {
                "ts": "1234567891.123",
                "thread_ts": "1234567890.123",
                "user": "U123",
                "text": "Reply message",
            },
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
            "email": "test@example.com",
        }

        history = [
            {
                "ts": "1234567890.123",
                "user": "U123",
                "text": "Check this out",
                "files": [{"id": "F123"}],
            }
        ]

        result = preprocess_history(history, slack_client)

        assert "Check this out" in result
        assert "[File attached]" in result

    def test_preprocess_with_people_cache(self):
        slack_client = Mock(spec=SlackClient)
        people_cache = {"U123": "Cached User"}

        history = [{"ts": "1234567890.123", "user": "U123", "text": "Message"}]

        result = preprocess_history(history, slack_client, people_cache)

        assert "Cached User" in result
        # Verify API was not called
        slack_client.get_user_info.assert_not_called()

    def test_preprocess_skips_messages_without_text_or_files(self):
        slack_client = Mock(spec=SlackClient)

        history = [{"ts": "1234567890.123", "user": "U123", "text": "", "files": None}]

        result = preprocess_history(history, slack_client)

        # Empty result or no user mention
        assert "U123" not in result or result.strip() == ""

    def test_preprocess_replaces_user_ids_in_message_text(self):
        """Test that user IDs in message text are replaced with names."""
        slack_client = Mock(spec=SlackClient)

        def get_user_info_side_effect(user_id):
            if user_id == "U123":
                return {"slackId": "U123", "displayName": "Test User", "email": "test@example.com"}
            elif user_id == "U456":
                return {
                    "slackId": "U456",
                    "displayName": "Alice Smith",
                    "email": "alice@example.com",
                }
            return None

        slack_client.get_user_info.side_effect = get_user_info_side_effect

        history = [
            {
                "ts": "1234567890.123",
                "user": "U123",
                "text": "Hey <@U456>, can you check this?",
            }
        ]

        result = preprocess_history(history, slack_client)

        assert "@Alice Smith" in result
        assert "<@U456>" not in result
        assert "U456" not in result

    def test_preprocess_replaces_multiple_user_ids(self):
        """Test that multiple user IDs in one message are replaced."""
        slack_client = Mock(spec=SlackClient)

        def get_user_info_side_effect(user_id):
            if user_id == "U123":
                return {"slackId": "U123", "displayName": "Test User", "email": "test@example.com"}
            elif user_id == "U456":
                return {"slackId": "U456", "displayName": "Alice", "email": "alice@example.com"}
            elif user_id == "U789":
                return {"slackId": "U789", "displayName": "Bob", "email": "bob@example.com"}
            return None

        slack_client.get_user_info.side_effect = get_user_info_side_effect

        history = [
            {
                "ts": "1234567890.123",
                "user": "U123",
                "text": "Hey <@U456> and <@U789>, can you help?",
            }
        ]

        result = preprocess_history(history, slack_client)

        assert "@Alice" in result
        assert "@Bob" in result
        assert "<@U456>" not in result
        assert "<@U789>" not in result


class TestReplaceUserIdsInText:
    """Tests for replace_user_ids_in_text function."""

    def test_replace_user_id_with_angle_brackets(self):
        """Test replacing <@U123> format."""
        slack_client = Mock(spec=SlackClient)
        slack_client.get_user_info.return_value = {
            "slackId": "U123",
            "displayName": "John Doe",
            "email": "john@example.com",
        }

        text = "Hey <@U123>, how are you?"
        result = replace_user_ids_in_text(text, slack_client)

        assert "@John Doe" in result
        assert "<@U123>" not in result

    def test_replace_user_id_without_angle_brackets(self):
        """Test replacing @U123 format."""
        slack_client = Mock(spec=SlackClient)
        slack_client.get_user_info.return_value = {
            "slackId": "U123",
            "displayName": "John Doe",
            "email": "john@example.com",
        }

        text = "Hey @U123, how are you?"
        result = replace_user_ids_in_text(text, slack_client)

        assert "@John Doe" in result
        assert "@U123" not in result

    def test_replace_multiple_user_ids(self):
        """Test replacing multiple user IDs in one message."""
        slack_client = Mock(spec=SlackClient)
        slack_client.get_user_info.side_effect = [
            {"slackId": "U123", "displayName": "Alice", "email": "alice@example.com"},
            {"slackId": "U456", "displayName": "Bob", "email": "bob@example.com"},
        ]

        text = "Hey <@U123> and <@U456>, can you help?"
        result = replace_user_ids_in_text(text, slack_client)

        assert "@Alice" in result
        assert "@Bob" in result
        assert "<@U123>" not in result
        assert "<@U456>" not in result

    def test_replace_with_cache(self):
        """Test that cache is used when available."""
        slack_client = Mock(spec=SlackClient)
        people_cache = {"U123": "Cached Name"}

        text = "Hey <@U123>, how are you?"
        result = replace_user_ids_in_text(text, slack_client, people_cache)

        assert "@Cached Name" in result
        # Verify API was not called
        slack_client.get_user_info.assert_not_called()

    def test_replace_updates_cache(self):
        """Test that cache is updated when looking up new users."""
        slack_client = Mock(spec=SlackClient)
        slack_client.get_user_info.return_value = {
            "slackId": "U123",
            "displayName": "John Doe",
            "email": "john@example.com",
        }
        people_cache = {}

        text = "Hey <@U123>, how are you?"
        result = replace_user_ids_in_text(text, slack_client, people_cache)

        assert "@John Doe" in result
        assert people_cache.get("U123") == "John Doe"

    def test_replace_handles_failed_lookup(self):
        """Test that failed user lookups keep the original ID."""
        slack_client = Mock(spec=SlackClient)
        slack_client.get_user_info.return_value = None

        text = "Hey <@U123>, how are you?"
        result = replace_user_ids_in_text(text, slack_client)

        # Should keep the original format or ID
        assert "U123" in result

    def test_replace_empty_text(self):
        """Test that empty text returns empty string."""
        slack_client = Mock(spec=SlackClient)
        result = replace_user_ids_in_text("", slack_client)
        assert result == ""

    def test_replace_no_user_ids(self):
        """Test that text without user IDs is unchanged."""
        slack_client = Mock(spec=SlackClient)
        text = "This is a normal message without any mentions."
        result = replace_user_ids_in_text(text, slack_client)
        assert result == text
        slack_client.get_user_info.assert_not_called()

    def test_replace_mixed_formats(self):
        """Test replacing both <@U123> and @U456 formats in same message."""
        slack_client = Mock(spec=SlackClient)
        slack_client.get_user_info.side_effect = [
            {"slackId": "U123", "displayName": "Alice", "email": "alice@example.com"},
            {"slackId": "U456", "displayName": "Bob", "email": "bob@example.com"},
        ]

        text = "Hey <@U123> and @U456, can you help?"
        result = replace_user_ids_in_text(text, slack_client)

        assert "@Alice" in result
        assert "@Bob" in result
        assert "<@U123>" not in result
        assert "@U456" not in result


class TestGroupMessagesByDate:
    """Tests for group_messages_by_date function."""

    def test_group_single_date(self):
        """Test grouping messages from the same date."""
        base_ts = datetime(2023, 1, 15, 12, 0, 0, tzinfo=timezone.utc).timestamp()
        history = [
            {"ts": str(base_ts), "text": "Message 1"},
            {"ts": str(base_ts + 3600), "text": "Message 2"},
            {"ts": str(base_ts + 7200), "text": "Message 3"},
        ]

        result = group_messages_by_date(history)

        assert len(result) == 1
        assert "20230115" in result
        assert len(result["20230115"]) == 3

    def test_group_multiple_dates(self):
        """Test grouping messages from different dates."""
        base_date = datetime(2023, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
        history = [
            {"ts": str(base_date.timestamp()), "text": "Jan 15 message"},
            {"ts": str((base_date.replace(day=16)).timestamp()), "text": "Jan 16 message"},
            {"ts": str((base_date.replace(day=17)).timestamp()), "text": "Jan 17 message"},
        ]

        result = group_messages_by_date(history)

        assert len(result) == 3
        assert "20230115" in result
        assert "20230116" in result
        assert "20230117" in result
        assert len(result["20230115"]) == 1
        assert len(result["20230116"]) == 1
        assert len(result["20230117"]) == 1

    def test_group_sorts_messages_within_date(self):
        """Test that messages within a date are sorted by timestamp."""
        base_ts = datetime(2023, 1, 15, 12, 0, 0, tzinfo=timezone.utc).timestamp()
        history = [
            {"ts": str(base_ts + 7200), "text": "Message 3"},
            {"ts": str(base_ts), "text": "Message 1"},
            {"ts": str(base_ts + 3600), "text": "Message 2"},
        ]

        result = group_messages_by_date(history)

        assert len(result) == 1
        messages = result["20230115"]
        assert len(messages) == 3
        # Verify sorted order
        assert float(messages[0]["ts"]) < float(messages[1]["ts"])
        assert float(messages[1]["ts"]) < float(messages[2]["ts"])

    def test_group_skips_messages_without_timestamp(self):
        """Test that messages without timestamps are skipped."""
        base_ts = datetime(2023, 1, 15, 12, 0, 0, tzinfo=timezone.utc).timestamp()
        history = [
            {"ts": str(base_ts), "text": "Valid message"},
            {"text": "No timestamp"},
            {"ts": None, "text": "None timestamp"},
        ]

        result = group_messages_by_date(history)

        assert len(result) == 1
        assert "20230115" in result
        assert len(result["20230115"]) == 1
        assert result["20230115"][0]["text"] == "Valid message"

    def test_group_handles_invalid_timestamps(self):
        """Test that invalid timestamps are skipped."""
        base_ts = datetime(2023, 1, 15, 12, 0, 0, tzinfo=timezone.utc).timestamp()
        history = [
            {"ts": str(base_ts), "text": "Valid message"},
            {"ts": "invalid", "text": "Invalid timestamp"},
            {"ts": "-1", "text": "Negative timestamp"},
        ]

        result = group_messages_by_date(history)

        assert len(result) == 1
        assert "20230115" in result
        assert len(result["20230115"]) == 1
        assert result["20230115"][0]["text"] == "Valid message"

    def test_group_empty_history(self):
        """Test grouping empty history."""
        result = group_messages_by_date([])
        assert result == {}

    def test_group_across_year_boundary(self):
        """Test grouping messages across year boundary."""
        dec_31 = datetime(2022, 12, 31, 12, 0, 0, tzinfo=timezone.utc)
        jan_1 = datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        history = [
            {"ts": str(dec_31.timestamp()), "text": "Dec 31"},
            {"ts": str(jan_1.timestamp()), "text": "Jan 1"},
        ]

        result = group_messages_by_date(history)

        assert len(result) == 2
        assert "20221231" in result
        assert "20230101" in result


class TestGetConversationDisplayName:
    """Tests for get_conversation_display_name function."""

    def test_channel_with_display_name(self):
        slack_client = Mock(spec=SlackClient)
        channel_info = {"id": "C123456", "displayName": "general"}

        result = get_conversation_display_name(channel_info, slack_client)
        assert result == "general"

    def test_channel_with_name(self):
        slack_client = Mock(spec=SlackClient)
        channel_info = {"id": "C123456", "name": "random"}

        result = get_conversation_display_name(channel_info, slack_client)
        assert result == "random"

    def test_dm_conversation(self):
        slack_client = Mock(spec=SlackClient)
        slack_client.get_user_info.return_value = {
            "slackId": "U123",
            "displayName": "John Doe",
            "email": "john@example.com",
        }

        channel_info = {"id": "D123456", "is_im": True, "user": "U123"}

        result = get_conversation_display_name(channel_info, slack_client)
        assert result == "John Doe"

    def test_dm_without_user_info(self):
        slack_client = Mock(spec=SlackClient)
        slack_client.get_user_info.return_value = None

        channel_info = {"id": "D123456", "is_im": True, "user": "U123"}

        result = get_conversation_display_name(channel_info, slack_client)
        assert result.startswith("dm_")
        assert "123456"[:8] in result

    def test_group_dm(self):
        slack_client = Mock(spec=SlackClient)
        slack_client.get_user_info.side_effect = [
            {"slackId": "U123", "displayName": "Alice", "email": "alice@example.com"},
            {"slackId": "U456", "displayName": "Bob", "email": "bob@example.com"},
        ]

        channel_info = {"id": "G123456", "is_mpim": True, "members": ["U123", "U456"]}

        result = get_conversation_display_name(channel_info, slack_client)
        # Should contain both names, sorted
        assert "Alice" in result
        assert "Bob" in result
        assert "," in result

    def test_group_dm_no_members(self):
        slack_client = Mock(spec=SlackClient)
        # Mock get_channel_members to return empty list (simulating no members found)
        slack_client.get_channel_members.return_value = []

        channel_info = {"id": "G123456", "is_mpim": True, "members": []}

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
            {"slackId": "U456", "displayName": "Bob", "email": "bob@example.com"},
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
        channel_info = {"id": "C123456"}

        result = get_conversation_display_name(channel_info, slack_client)
        assert result == "C123456"

    def test_empty_name_fallback(self):
        slack_client = Mock(spec=SlackClient)
        channel_info = {"id": "C123456", "name": ""}

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
            assert current_end < next_start or (
                current_end.year == next_start.year and current_end.month < next_start.month
            )


class TestShouldShareWithMember:
    """Tests for _should_share_with_member function."""

    def test_no_share_members_list_shares_with_all(self):
        """Test that None shareMembers list shares with all (backward compatible)."""
        user_info = {
            "slackId": "U123",
            "email": "user@example.com",
            "displayName": "Test User",
        }
        result = _should_share_with_member("U123", user_info, None)
        assert result is True

    def test_empty_share_members_list_shares_with_all(self):
        """Test that empty shareMembers list shares with all."""
        user_info = {
            "slackId": "U123",
            "email": "user@example.com",
            "displayName": "Test User",
        }
        result = _should_share_with_member("U123", user_info, [])
        assert result is True

    def test_match_by_slack_id(self):
        """Test matching by Slack user ID."""
        user_info = {
            "slackId": "U123",
            "email": "user@example.com",
            "displayName": "Test User",
        }
        share_members = ["U123", "U456"]
        result = _should_share_with_member("U123", user_info, share_members)
        assert result is True

    def test_match_by_email(self):
        """Test matching by email address."""
        user_info = {
            "slackId": "U123",
            "email": "user@example.com",
            "displayName": "Test User",
        }
        share_members = ["other@example.com", "user@example.com"]
        result = _should_share_with_member("U123", user_info, share_members)
        assert result is True

    def test_match_by_display_name(self):
        """Test matching by display name."""
        user_info = {
            "slackId": "U123",
            "email": "user@example.com",
            "displayName": "Test User",
        }
        share_members = ["Other User", "Test User", "Another User"]
        result = _should_share_with_member("U123", user_info, share_members)
        assert result is True

    def test_case_insensitive_matching(self):
        """Test that matching is case-insensitive."""
        user_info = {
            "slackId": "U123",
            "email": "User@Example.COM",
            "displayName": "Test User",
        }
        # Test case-insensitive email matching
        result1 = _should_share_with_member("U123", user_info, ["user@example.com"])
        assert result1 is True

        # Test case-insensitive display name matching
        result2 = _should_share_with_member("U123", user_info, ["test user"])
        assert result2 is True

        # Test case-insensitive Slack ID matching
        result3 = _should_share_with_member("U123", user_info, ["u123"])
        assert result3 is True

    def test_no_match_excludes_member(self):
        """Test that members not in list are excluded."""
        user_info = {
            "slackId": "U123",
            "email": "user@example.com",
            "displayName": "Test User",
        }
        share_members = ["U456", "other@example.com", "Other User"]
        result = _should_share_with_member("U123", user_info, share_members)
        assert result is False

    def test_no_user_info_excludes(self):
        """Test that missing user info excludes member."""
        result = _should_share_with_member("U123", None, ["U123"])
        assert result is False

    def test_mixed_identifier_types(self):
        """Test that shareMembers can contain mixed identifier types."""
        user_info = {
            "slackId": "U123",
            "email": "user@example.com",
            "displayName": "Test User",
        }
        # Mix of IDs, emails, and names
        share_members = ["U456", "other@example.com", "Test User", "U789"]
        result = _should_share_with_member("U123", user_info, share_members)
        assert result is True  # Matches by display name

    def test_whitespace_handling(self):
        """Test that whitespace in identifiers is handled correctly."""
        user_info = {
            "slackId": "U123",
            "email": "user@example.com",
            "displayName": "Test User",
        }
        # Identifiers with extra whitespace
        share_members = ["  U123  ", "  Test User  ", "  user@example.com  "]
        result = _should_share_with_member("U123", user_info, share_members)
        assert result is True

    def test_empty_strings_in_list_ignored(self):
        """Test that empty strings in shareMembers are ignored."""
        user_info = {
            "slackId": "U123",
            "email": "user@example.com",
            "displayName": "Test User",
        }
        share_members = ["", "U123", "  ", None]
        result = _should_share_with_member("U123", user_info, share_members)
        assert result is True  # Should match U123


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
        assert result == len(text.encode("utf-8"))
        assert result == 13

    def test_unicode_string(self):
        """Should handle Unicode characters correctly."""
        # Use euro sign which takes 3 bytes in UTF-8
        text = "Price: \u20ac100"  # ? symbol
        result = estimate_file_size(text)
        expected = len(text.encode("utf-8"))
        assert result == expected
        # UTF-8 encoding: "Price: " = 7 bytes, "?" = 3 bytes, "100" = 3 bytes = 13 bytes total
        # Character count: 11 characters
        assert result == 13  # Verify exact byte count
        assert result > len(
            text
        )  # Byte count should be larger than character count for multi-byte Unicode

    def test_multiline_string(self):
        """Should handle multiline strings."""
        text = "Line 1\nLine 2\nLine 3"
        result = estimate_file_size(text)
        assert result == len(text.encode("utf-8"))

    def test_large_string(self):
        """Should handle large strings."""
        text = "A" * 10000
        result = estimate_file_size(text)
        assert result == 10000  # Each 'A' is 1 byte in UTF-8


class TestLoadBrowserExportConfig:
    """Tests for load_browser_export_config function."""

    def test_load_valid_config(self, tmp_path):
        """Test loading a valid browser-export.json file."""
        config_file = tmp_path / "browser-export.json"
        config_data = {
            "browser-export": [
                {
                    "id": "D1234567890",
                    "name": "Bob Smith, John Doe",
                    "is_im": True,
                    "is_mpim": False,
                    "export": True,
                    "share": True,
                }
            ]
        }
        config_file.write_text(json.dumps(config_data))

        result = load_browser_export_config(str(config_file))
        assert result is not None
        assert "browser-export" in result
        assert len(result["browser-export"]) == 1
        assert result["browser-export"][0]["id"] == "D1234567890"

    def test_load_nonexistent_file(self):
        """Test loading a nonexistent config file."""
        result = load_browser_export_config("nonexistent.json")
        assert result is None

    def test_load_missing_browser_export_key(self, tmp_path):
        """Test loading config with missing browser-export key."""
        config_file = tmp_path / "browser-export.json"
        config_file.write_text('{"invalid": "structure"}')

        result = load_browser_export_config(str(config_file))
        # Function returns {"browser-export": []} when browser-export key is missing
        # (uses default empty list)
        assert result is not None
        assert "browser-export" in result
        assert result["browser-export"] == []

    def test_load_not_list(self, tmp_path):
        """Test loading config where browser-export is not a list."""
        config_file = tmp_path / "browser-export.json"
        config_file.write_text('{"browser-export": "not a list"}')

        result = load_browser_export_config(str(config_file))
        assert result is None


class TestFindConversationInConfig:
    """Tests for find_conversation_in_config function."""

    def test_find_by_id(self):
        """Test finding conversation by ID."""
        config_data = {
            "browser-export": [
                {
                    "id": "D1234567890",
                    "name": "Bob Smith, John Doe",
                    "is_im": True,
                },
                {
                    "id": "C1234567890",
                    "name": "Carol White, David Brown, John Doe",
                    "is_mpim": True,
                },
            ]
        }

        result = find_conversation_in_config(config_data, conversation_id="D1234567890")
        assert result is not None
        assert result["id"] == "D1234567890"
        assert result["name"] == "Bob Smith, John Doe"

    def test_find_by_name(self):
        """Test finding conversation by name."""
        config_data = {
            "browser-export": [
                {
                    "id": "D1234567890",
                    "name": "Bob Smith, John Doe",
                    "is_im": True,
                },
            ]
        }

        result = find_conversation_in_config(
            config_data, conversation_name="Bob Smith, John Doe"
        )
        assert result is not None
        assert result["id"] == "D1234567890"
        assert result["name"] == "Bob Smith, John Doe"

    def test_find_not_found(self):
        """Test finding conversation that doesn't exist."""
        config_data = {"browser-export": []}

        result = find_conversation_in_config(config_data, conversation_id="INVALID")
        assert result is None

    def test_find_empty_config(self):
        """Test finding in empty config."""
        result = find_conversation_in_config(None, conversation_id="D1234567890")
        assert result is None

    def test_find_prefers_id_over_name(self):
        """Test that ID search takes precedence."""
        config_data = {
            "browser-export": [
                {
                    "id": "D1234567890",
                    "name": "Bob Smith, John Doe",
                    "is_im": True,
                },
                {
                    "id": "C1234567890",
                    "name": "Bob Smith, John Doe",  # Same name, different ID
                    "is_mpim": True,
                },
            ]
        }

        result = find_conversation_in_config(
            config_data, conversation_id="D1234567890", conversation_name="Bob Smith, John Doe"
        )
        assert result is not None
        assert result["id"] == "D1234567890"  # Should find by ID first


class TestInitializeStats:
    """Tests for _initialize_stats function."""

    def test_initialize_stats_returns_all_keys(self):
        """Test that _initialize_stats returns all required keys."""
        stats = _initialize_stats()
        assert isinstance(stats, dict)
        assert "processed" in stats
        assert "skipped" in stats
        assert "failed" in stats
        assert "uploaded" in stats
        assert "upload_failed" in stats
        assert "shared" in stats
        assert "share_failed" in stats
        assert "total_messages" in stats
        assert all(v == 0 for v in stats.values())


class TestFilterMessagesByDateRange:
    """Tests for filter_messages_by_date_range function."""

    def test_filter_no_timestamps_returns_all(self):
        """Test that filtering with no timestamps returns all messages."""
        messages = [
            {"ts": "1729263032.513419", "text": "Message 1"},
            {"ts": "1729263033.513419", "text": "Message 2"},
        ]
        filtered, error = filter_messages_by_date_range(messages, None, None)
        assert error is None
        assert len(filtered) == 2

    def test_filter_by_oldest_timestamp(self):
        """Test filtering by oldest timestamp."""
        messages = [
            {"ts": "1729263032.513419", "text": "Message 1"},
            {"ts": "1729263033.513419", "text": "Message 2"},
            {"ts": "1729263034.513419", "text": "Message 3"},
        ]
        # Filter to only messages after 1729263033.0
        filtered, error = filter_messages_by_date_range(
            messages, oldest_ts="1729263033.0", latest_ts=None
        )
        assert error is None
        assert len(filtered) == 2
        assert filtered[0]["text"] == "Message 2"
        assert filtered[1]["text"] == "Message 3"

    def test_filter_by_latest_timestamp(self):
        """Test filtering by latest timestamp."""
        messages = [
            {"ts": "1729263032.513419", "text": "Message 1"},
            {"ts": "1729263033.513419", "text": "Message 2"},
            {"ts": "1729263034.513419", "text": "Message 3"},
        ]
        # Filter to only messages before or equal to 1729263033.513419 (Message 2's timestamp)
        filtered, error = filter_messages_by_date_range(
            messages, oldest_ts=None, latest_ts="1729263033.513419"
        )
        assert error is None
        assert len(filtered) == 2  # Messages 1 and 2 (both <= Message 2's timestamp)
        assert filtered[0]["text"] == "Message 1"
        assert filtered[1]["text"] == "Message 2"

    def test_filter_by_date_range(self):
        """Test filtering by both oldest and latest timestamp."""
        messages = [
            {"ts": "1729263032.513419", "text": "Message 1"},
            {"ts": "1729263033.513419", "text": "Message 2"},
            {"ts": "1729263034.513419", "text": "Message 3"},
        ]
        # Filter to messages between 1729263032.0 and 1729263033.0 (inclusive)
        # Message 1 (Message 1 is in range, Message 2 is excluded)
        filtered, error = filter_messages_by_date_range(
            messages, oldest_ts="1729263032.0", latest_ts="1729263033.0"
        )
        assert error is None
        # Message 1 (1729263032.513419) is >= 1729263032.0 and <= 1729263033.0, so included
        # Message 2 (1729263033.513419) is > 1729263033.0, so excluded
        # Message 3 (1729263034.513419) is > 1729263033.0, so excluded
        assert len(filtered) == 1
        assert filtered[0]["text"] == "Message 1"

    def test_validate_range_start_after_end(self):
        """Test that validation catches start date after end date."""
        messages = []
        filtered, error = filter_messages_by_date_range(
            messages, oldest_ts="1729263034.0", latest_ts="1729263032.0", validate_range=True
        )
        assert error is not None
        assert "must be before" in error.lower()
        assert len(filtered) == 0

    def test_validate_max_date_range(self):
        """Test validation of maximum date range."""
        messages = []
        filtered, error = filter_messages_by_date_range(
            messages,
            oldest_ts="1729263032.0",
            latest_ts="1729263032.0",
            validate_range=True,
            max_date_range_days=365,
        )
        assert error is None  # Same timestamp = 0 days, should pass

        # Test with range exceeding max
        SECONDS_PER_DAY = 86400
        old_ts = "1729263032.0"
        new_ts = str(float(old_ts) + (400 * SECONDS_PER_DAY))  # 400 days
        filtered, error = filter_messages_by_date_range(
            messages,
            oldest_ts=old_ts,
            latest_ts=new_ts,
            validate_range=True,
            max_date_range_days=365,
        )
        assert error is not None
        assert "exceeds maximum" in error.lower()

    def test_filter_messages_without_timestamp(self):
        """Test that messages without timestamp are excluded."""
        messages = [
            {"ts": "1729263032.513419", "text": "Message 1"},
            {"text": "Message without timestamp"},
            {"ts": "1729263033.513419", "text": "Message 2"},
        ]
        filtered, error = filter_messages_by_date_range(
            messages, oldest_ts="1729263032.0", latest_ts="1729263034.0"
        )
        assert error is None
        assert len(filtered) == 2  # Only messages with timestamps


class TestGetConversationMembers:
    """Tests for _get_conversation_members function."""

    def test_get_channel_members(self):
        """Test getting members for a regular channel."""
        slack_client = Mock(spec=SlackClient)
        slack_client.get_channel_members.return_value = ["U123", "U456"]

        conversation_info = {}  # Not a DM or group DM
        members = _get_conversation_members(slack_client, "C123456", conversation_info)

        assert members == ["U123", "U456"]
        slack_client.get_channel_members.assert_called_once_with("C123456")

    def test_get_dm_members_from_user_field(self):
        """Test getting DM member from user field."""
        slack_client = Mock(spec=SlackClient)

        conversation_info = {"is_im": True, "user": "U123"}
        members = _get_conversation_members(slack_client, "D123456", conversation_info)

        assert members == ["U123"]
        slack_client.get_channel_members.assert_not_called()

    def test_get_dm_members_from_api(self):
        """Test getting DM member via API when user field missing."""
        slack_client = Mock(spec=SlackClient)
        slack_client.client = Mock()
        slack_client.client.conversations_info.return_value = {
            "ok": True,
            "channel": {"user": "U123"},
        }

        conversation_info = {"is_im": True}  # No user field
        members = _get_conversation_members(slack_client, "D123456", conversation_info)

        assert members == ["U123"]
        slack_client.client.conversations_info.assert_called_once_with(channel="D123456")

    def test_get_group_dm_members(self):
        """Test getting members for a group DM."""
        slack_client = Mock(spec=SlackClient)
        slack_client.get_channel_members.return_value = ["U123", "U456", "U789"]

        conversation_info = {"is_mpim": True}
        members = _get_conversation_members(slack_client, "G123456", conversation_info)

        assert members == ["U123", "U456", "U789"]
        slack_client.get_channel_members.assert_called_once_with("G123456")

    def test_get_dm_members_api_failure(self):
        """Test handling API failure when getting DM member."""
        slack_client = Mock(spec=SlackClient)
        slack_client.client = Mock()
        slack_client.client.conversations_info.side_effect = Exception("API Error")

        conversation_info = {"is_im": True}  # No user field
        members = _get_conversation_members(slack_client, "D123456", conversation_info)

        assert members == []  # Should return empty list on failure


class TestGetOldestTimestampForExport:
    """Tests for get_oldest_timestamp_for_export function."""

    def test_no_explicit_date_no_drive(self):
        """Test with no explicit date and not uploading to Drive."""
        result = get_oldest_timestamp_for_export(
            google_drive_client=None,
            folder_id=None,
            conversation_name="Test Channel",
            explicit_start_date=None,
            upload_to_drive=False,
        )
        assert result is None

    def test_explicit_date_no_drive(self):
        """Test with explicit date and not uploading to Drive."""
        result = get_oldest_timestamp_for_export(
            google_drive_client=None,
            folder_id=None,
            conversation_name="Test Channel",
            explicit_start_date="2024-01-01",
            upload_to_drive=False,
        )
        assert result is not None
        assert isinstance(result, str)

    def test_explicit_date_with_drive_no_metadata(self):
        """Test with explicit date and Drive but no previous export."""
        google_drive_client = Mock()
        google_drive_client.create_folder.return_value = "folder123"
        google_drive_client.get_latest_export_timestamp.return_value = None

        result = get_oldest_timestamp_for_export(
            google_drive_client=google_drive_client,
            folder_id=None,
            conversation_name="Test Channel",
            explicit_start_date="2024-01-01",
            upload_to_drive=True,
            sanitized_folder_name="test-channel",
            safe_conversation_name="test-channel",
        )
        assert result is not None
        assert isinstance(result, str)

    def test_drive_metadata_no_explicit_date(self):
        """Test with Drive metadata but no explicit date."""
        google_drive_client = Mock()
        google_drive_client.create_folder.return_value = "folder123"
        google_drive_client.get_latest_export_timestamp.return_value = "1729263032.0"

        result = get_oldest_timestamp_for_export(
            google_drive_client=google_drive_client,
            folder_id="folder123",
            conversation_name="Test Channel",
            explicit_start_date=None,
            upload_to_drive=True,
            safe_conversation_name="test-channel",
        )
        assert result == "1729263032.0"

    def test_drive_metadata_later_than_explicit_date(self):
        """Test when Drive metadata is later than explicit date."""
        google_drive_client = Mock()
        google_drive_client.get_latest_export_timestamp.return_value = "1729263034.0"

        result = get_oldest_timestamp_for_export(
            google_drive_client=google_drive_client,
            folder_id="folder123",
            conversation_name="Test Channel",
            explicit_start_date="2024-10-18",  # Converts to ~1729263032.0
            upload_to_drive=True,
            safe_conversation_name="test-channel",
        )
        # Should use the later of the two (Drive metadata)
        assert result == "1729263034.0"

    def test_explicit_date_later_than_drive_metadata(self):
        """Test when explicit date is later than Drive metadata."""
        google_drive_client = Mock()
        google_drive_client.get_latest_export_timestamp.return_value = "1729263032.0"

        # Use a date that converts to a timestamp later than 1729263032.0
        result = get_oldest_timestamp_for_export(
            google_drive_client=google_drive_client,
            folder_id="folder123",
            conversation_name="Test Channel",
            explicit_start_date="2024-10-20",  # Later date
            upload_to_drive=True,
            safe_conversation_name="test-channel",
        )
        # Should use the later of the two (explicit date)
        assert result is not None
        assert float(result) >= 1729263032.0
