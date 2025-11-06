"""
Unit tests for main.py functions.

Tests cover preprocessing and conversation display name logic.
"""
import pytest
from unittest.mock import Mock, patch
from src.main import preprocess_history, get_conversation_display_name
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
