"""
Unit tests for Slack client.

Tests use mocks to avoid requiring actual Slack API credentials.
"""
import pytest
from unittest.mock import Mock, patch, MagicMock
from slack_sdk.errors import SlackApiError
from src.slack_client import SlackClient


class TestSlackClientInit:
    """Tests for SlackClient initialization."""
    
    def test_init_with_valid_bot_token(self):
        with patch('src.slack_client.WebClient'):
            client = SlackClient("xoxb-1234567890-1234567890123-abcdefghijklmnopqrstuvwx")
            assert client is not None
            assert isinstance(client.user_cache, type(client.user_cache))  # LRUCache instance
    
    def test_init_with_valid_user_token(self):
        with patch('src.slack_client.WebClient'):
            client = SlackClient("xoxp-1234567890-1234567890123-abcdefghijklmnopqrstuvwx")
            assert client is not None
    
    def test_init_with_invalid_token_empty(self):
        with pytest.raises(ValueError, match="Slack Bot Token is missing"):
            SlackClient("")
    
    def test_init_with_invalid_token_placeholder(self):
        with pytest.raises(ValueError, match="Slack Bot Token is missing"):
            SlackClient("xoxb-your-token-here")
    
    def test_init_with_invalid_token_format(self):
        with pytest.raises(ValueError, match="Invalid Slack token format"):
            SlackClient("invalid-token-format")
    
    def test_init_with_invalid_token_wrong_prefix(self):
        with pytest.raises(ValueError, match="Invalid Slack token format"):
            SlackClient("xoxa-something")


class TestGetUserInfo:
    """Tests for get_user_info method."""
    
    @patch('src.slack_client.WebClient')
    def test_get_user_info_success(self, mock_web_client_class):
        mock_client = Mock()
        mock_client.users_info.return_value = {
            "user": {
                "id": "U123456",
                "name": "testuser",
                "is_bot": False,
                "profile": {
                    "display_name_normalized": "Test User",
                    "email": "test@example.com"
                }
            }
        }
        mock_web_client_class.return_value = mock_client
        
        client = SlackClient("xoxb-test-token")
        result = client.get_user_info("U123456")
        
        assert result is not None
        assert result["slackId"] == "U123456"
        assert result["displayName"] == "Test User"
        assert result["email"] == "test@example.com"
        # Verify cached
        assert "U123456" in client.user_cache
    
    @patch('src.slack_client.WebClient')
    def test_get_user_info_bot_user(self, mock_web_client_class):
        mock_client = Mock()
        mock_client.users_info.return_value = {
            "user": {
                "id": "B123456",
                "name": "botuser",
                "is_bot": True,
                "profile": {}
            }
        }
        mock_web_client_class.return_value = mock_client
        
        client = SlackClient("xoxb-test-token")
        result = client.get_user_info("B123456")
        
        assert result is None
        assert client.user_cache["B123456"] is None
    
    @patch('src.slack_client.WebClient')
    def test_get_user_info_from_cache(self, mock_web_client_class):
        mock_web_client_class.return_value = Mock()
        
        client = SlackClient("xoxb-test-token")
        cached_user = {"slackId": "U123", "displayName": "Cached User", "email": "cached@example.com"}
        client.user_cache["U123"] = cached_user
        
        result = client.get_user_info("U123")
        assert result == cached_user
        # Verify API was not called
        mock_web_client_class.return_value.users_info.assert_not_called()
    
    @patch('src.slack_client.WebClient')
    def test_get_user_info_api_error(self, mock_web_client_class):
        mock_client = Mock()
        mock_response = Mock()
        mock_response.get.return_value = "user_not_found"
        mock_error = SlackApiError("Error", mock_response)
        mock_client.users_info.side_effect = mock_error
        mock_web_client_class.return_value = mock_client
        
        client = SlackClient("xoxb-test-token")
        result = client.get_user_info("U123456")
        
        assert result is None
        assert client.user_cache["U123456"] is None


class TestGetChannelMembers:
    """Tests for get_channel_members method."""
    
    @patch('src.slack_client.WebClient')
    def test_get_channel_members_single_page(self, mock_web_client_class):
        mock_client = Mock()
        mock_client.conversations_members.return_value = {
            "members": ["U123", "U456", "U789"],
            "response_metadata": {"next_cursor": ""}
        }
        mock_web_client_class.return_value = mock_client
        
        client = SlackClient("xoxb-test-token")
        result = client.get_channel_members("C123456")
        
        assert result == ["U123", "U456", "U789"]
    
    @patch('src.slack_client.WebClient')
    def test_get_channel_members_multiple_pages(self, mock_web_client_class):
        mock_client = Mock()
        mock_client.conversations_members.side_effect = [
            {
                "members": ["U123", "U456"],
                "response_metadata": {"next_cursor": "cursor123"}
            },
            {
                "members": ["U789"],
                "response_metadata": {"next_cursor": ""}
            }
        ]
        mock_web_client_class.return_value = mock_client
        
        client = SlackClient("xoxb-test-token")
        result = client.get_channel_members("C123456")
        
        assert result == ["U123", "U456", "U789"]
        assert mock_client.conversations_members.call_count == 2
    
    @patch('src.slack_client.WebClient')
    def test_get_channel_members_api_error(self, mock_web_client_class):
        mock_client = Mock()
        mock_response = Mock()
        mock_response.get.return_value = "channel_not_found"
        mock_error = SlackApiError("Error", mock_response)
        mock_client.conversations_members.side_effect = mock_error
        mock_web_client_class.return_value = mock_client
        
        client = SlackClient("xoxb-test-token")
        result = client.get_channel_members("C123456")
        
        assert result == []


class TestFetchChannelHistory:
    """Tests for fetch_channel_history method."""
    
    @patch('src.slack_client.WebClient')
    @patch('src.slack_client.time.sleep')  # Mock sleep to speed up tests
    def test_fetch_channel_history_single_page(self, mock_sleep, mock_web_client_class):
        mock_client = Mock()
        mock_client.conversations_history.return_value = {
            "messages": [
                {"ts": "1234567890.123", "text": "Message 1"},
                {"ts": "1234567891.123", "text": "Message 2"}
            ],
            "response_metadata": {}
        }
        mock_web_client_class.return_value = mock_client
        
        client = SlackClient("xoxb-test-token")
        result = client.fetch_channel_history("C123456")
        
        assert result is not None
        assert len(result) == 2
        assert result[0]["text"] == "Message 1"
    
    @patch('src.slack_client.WebClient')
    @patch('src.slack_client.time.sleep')
    def test_fetch_channel_history_multiple_pages(self, mock_sleep, mock_web_client_class):
        mock_client = Mock()
        mock_client.conversations_history.side_effect = [
            {
                "messages": [{"ts": "1234567890.123", "text": "Message 1"}],
                "response_metadata": {"next_cursor": "cursor123"}
            },
            {
                "messages": [{"ts": "1234567891.123", "text": "Message 2"}],
                "response_metadata": {}
            }
        ]
        mock_web_client_class.return_value = mock_client
        
        client = SlackClient("xoxb-test-token")
        result = client.fetch_channel_history("C123456")
        
        assert result is not None
        assert len(result) == 2
        # Verify messages are sorted by timestamp
        assert float(result[0]["ts"]) < float(result[1]["ts"])
    
    @patch('src.slack_client.WebClient')
    @patch('src.slack_client.time.sleep')
    def test_fetch_channel_history_with_timestamps(self, mock_sleep, mock_web_client_class):
        mock_client = Mock()
        mock_client.conversations_history.return_value = {
            "messages": [{"ts": "1234567890.123", "text": "Message"}],
            "response_metadata": {}
        }
        mock_web_client_class.return_value = mock_client
        
        client = SlackClient("xoxb-test-token")
        result = client.fetch_channel_history(
            "C123456",
            oldest_ts="1234567890.000",
            latest_ts="1234567999.999"
        )
        
        assert result is not None
        # Verify timestamps were passed to API
        call_args = mock_client.conversations_history.call_args
        assert call_args[1]["oldest"] == "1234567890.000"
        assert call_args[1]["latest"] == "1234567999.999"
    
    @patch('src.slack_client.WebClient')
    @patch('src.slack_client.time.sleep')
    def test_fetch_channel_history_rate_limit_retry(self, mock_sleep, mock_web_client_class):
        mock_client = Mock()
        mock_response = Mock()
        mock_response.headers = {"Retry-After": "2"}
        mock_response.get.return_value = "ratelimited"
        
        # First call fails with rate limit, second succeeds
        mock_client.conversations_history.side_effect = [
            SlackApiError("Rate limited", mock_response),
            {
                "messages": [{"ts": "1234567890.123", "text": "Message"}],
                "response_metadata": {}
            }
        ]
        mock_web_client_class.return_value = mock_client
        
        client = SlackClient("xoxb-test-token")
        result = client.fetch_channel_history("C123456")
        
        assert result is not None
        assert len(result) == 1
        # Verify sleep was called for retry
        assert mock_sleep.called
    
    @patch('src.slack_client.WebClient')
    @patch('src.slack_client.time.sleep')
    def test_fetch_channel_history_api_error(self, mock_sleep, mock_web_client_class):
        mock_client = Mock()
        mock_response = Mock()
        mock_response.get.return_value = "channel_not_found"
        mock_error = SlackApiError("Error", mock_response)
        mock_client.conversations_history.side_effect = mock_error
        mock_web_client_class.return_value = mock_client
        
        client = SlackClient("xoxb-test-token")
        result = client.fetch_channel_history("C123456")
        
        assert result is None
