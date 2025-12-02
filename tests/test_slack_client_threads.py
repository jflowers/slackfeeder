import time
from unittest.mock import MagicMock, patch
import pytest
from slack_sdk.errors import SlackApiError
from src.slack_client import SlackClient

class TestSlackClientThreads:
    def setup_method(self):
        self.token = "xoxb-valid-token"
        self.client = SlackClient(self.token)
        self.mock_web_client = MagicMock()
        self.client.client = self.mock_web_client

    def test_fetch_thread_history_success(self):
        """Test fetching a thread successfully with pagination."""
        channel_id = "C12345"
        thread_ts = "1234567890.123456"
        
        # Mock responses for two pages of replies
        self.mock_web_client.conversations_replies.side_effect = [
            {
                "ok": True,
                "messages": [{"ts": "1234567890.123456", "text": "Root"}, {"ts": "1234567891.123456", "text": "Reply 1"}],
                "response_metadata": {"next_cursor": "cursor1"}
            },
            {
                "ok": True,
                "messages": [{"ts": "1234567892.123456", "text": "Reply 2"}],
                "response_metadata": {"next_cursor": ""}
            }
        ]

        result = self.client.fetch_thread_history(channel_id, thread_ts)

        assert len(result) == 3
        assert result[0]["text"] == "Root"
        assert result[2]["text"] == "Reply 2"
        assert self.mock_web_client.conversations_replies.call_count == 2

    def test_fetch_thread_history_rate_limit(self):
        """Test handling rate limits during thread fetch."""
        channel_id = "C12345"
        thread_ts = "1234567890.123456"

        # Mock the response object inside SlackApiError
        mock_response = MagicMock()
        mock_response.get.side_effect = lambda k, default=None: {"error": "ratelimited"}.get(k, default) # Emulate .get() for error code lookup
        mock_response.headers = {"Retry-After": "1"} # Emulate .headers attribute
        
        # Create the error with the mock response
        error_response = SlackApiError("Rate limited", mock_response)
        
        success_response = {
            "ok": True,
            "messages": [{"ts": thread_ts, "text": "Root"}],
            "response_metadata": {"next_cursor": ""}
        }

        self.mock_web_client.conversations_replies.side_effect = [error_response, success_response]

        with patch("time.sleep") as mock_sleep:
            result = self.client.fetch_thread_history(channel_id, thread_ts)

        assert len(result) == 1
        assert self.mock_web_client.conversations_replies.call_count == 2
        # Should sleep for retry
        mock_sleep.assert_called()

    def test_fetch_thread_history_error(self):
        """Test handling fatal error during thread fetch."""
        channel_id = "C12345"
        thread_ts = "1234567890.123456"

        self.mock_web_client.conversations_replies.side_effect = SlackApiError("Fatal error", {"error": "channel_not_found"})

        result = self.client.fetch_thread_history(channel_id, thread_ts)

        assert result is None
