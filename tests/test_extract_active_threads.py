import json
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

# Import the module under test
import scripts.extract_active_threads as extract_active_threads
from scripts.extract_active_threads import (
    extract_active_threads_for_daily_export,
    extract_thread_summary_metadata,
    expand_and_extract_thread_replies,
    navigate_to_threads_view,
    THREADS_SIDEBAR_BUTTON_UID,
    THREAD_SIDEPANEL_SELECTOR,
)

# Mock data
MOCK_THREAD_SUMMARY_JS_OUTPUT = {
    "threads": [
        {
            "thread_ts": "1700000000.123456",
            "conversation_id": "C1111111111",
            "last_reply_ts": 1700000000.123456,
            "title_snippet": "team-psce Alex Xuan and you replied to: Is this related...",
            "click_element_uid": "thread_click_1",
        },
    ]
}

MOCK_MESSAGES = {
    "ok": True,
    "messages": [
        {"ts": "1700000000.123456", "user": "U_TEST", "text": "Test message"}
    ]
}

class TestExtractActiveThreads:
    
    def setup_method(self):
        # Create a mock for evaluate_script
        self.mock_evaluate_script = MagicMock()
        self.mock_click = MagicMock()
        self.mock_press_key = MagicMock()

    def test_navigate_to_threads_view(self):
        """Test successful navigation to the Threads view."""
        result = navigate_to_threads_view(self.mock_click)
        self.mock_click.assert_called_once_with(uid=THREADS_SIDEBAR_BUTTON_UID)
        assert result is True

    @patch('scripts.extract_active_threads.time.sleep', return_value=None)
    def test_extract_thread_summary_metadata(self, mock_sleep):
        """Test extraction of thread summary metadata."""
        # Configure mock to return expected data when the specific JS helper is used
        def side_effect(function, args=None):
            # Check if the function string matches the helper output
            if function == extract_active_threads._get_js_extract_thread_summary_metadata():
                return MOCK_THREAD_SUMMARY_JS_OUTPUT
            return {}

        self.mock_evaluate_script.side_effect = side_effect
        
        target_conv_name = "team-psce"
        export_date_range = (datetime.now(timezone.utc), datetime.now(timezone.utc))

        summaries = extract_thread_summary_metadata(
            self.mock_evaluate_script, 
            target_conv_name, 
            export_date_range
        )

        assert len(summaries) == 1
        assert summaries[0]["thread_ts"] == "1700000000.123456"
        self.mock_evaluate_script.assert_called_once()

    @patch('scripts.extract_active_threads.time.sleep', return_value=None)
    @patch('scripts.extract_active_threads.extract_messages_from_dom')
    def test_expand_and_extract_thread_replies(self, mock_extract_dom, mock_sleep):
        """Test expanding replies and extracting messages."""
        # Setup mocks
        thread_info = {
            "thread_ts": "1700000000.123456", 
            "conversation_id": "C1", 
            "click_element_uid": "uid_1"
        }
        export_date_range = (
            datetime.fromtimestamp(1600000000, tz=timezone.utc), 
            datetime.fromtimestamp(1800000000, tz=timezone.utc)
        )

        # Mock extract_messages_from_dom to return messages
        mock_extract_dom.return_value = MOCK_MESSAGES

        # Mock evaluate_script to simulate finding/not finding buttons
        # 1. Find "Show more" button (returns dict)
        # 2. Find "Close" button (returns dict with result)
        # 3. Subsequent calls (if any)
        self.mock_evaluate_script.side_effect = [
            {"result": {"uid": "show_more_uid", "text": "Show more"}}, # Find show more
            {"result": "close_uid"}, # Find close button
        ]

        messages = expand_and_extract_thread_replies(
            self.mock_evaluate_script,
            self.mock_click,
            self.mock_press_key,
            thread_info,
            export_date_range
        )

        # Assertions
        assert len(messages) == 1
        assert messages[0]["text"] == "Test message"
        
        # Should click thread, show more, and close
        assert self.mock_click.call_count == 3
        self.mock_click.assert_any_call(uid="uid_1")
        self.mock_click.assert_any_call(uid="show_more_uid")
        self.mock_click.assert_any_call(uid="close_uid")

    @patch('scripts.extract_active_threads.time.sleep', return_value=None)
    @patch('scripts.extract_active_threads.navigate_to_threads_view', return_value=True)
    @patch('scripts.extract_active_threads.extract_thread_summary_metadata')
    @patch('scripts.extract_active_threads.expand_and_extract_thread_replies')
    def test_extract_active_threads_orchestration(
        self,
        mock_expand,
        mock_extract_summaries,
        mock_navigate,
        mock_sleep
    ):
        """Test the main orchestration function."""
        # Setup
        # We need enough empty lists to trigger the "consecutive_no_new_threads >= 5" stop condition
        # 1st call: Returns threads (reset counter)
        # 2nd-7th calls: Return empty (increment counter to 5)
        mock_extract_summaries.side_effect = [
            MOCK_THREAD_SUMMARY_JS_OUTPUT["threads"], 
            [], [], [], [], [], [] 
        ]
        mock_expand.return_value = MOCK_MESSAGES["messages"]

        result = extract_active_threads_for_daily_export(
            self.mock_evaluate_script,
            self.mock_click,
            self.mock_press_key,
            "target_conv",
            datetime.now(timezone.utc)
        )

        assert len(result) == 1
        assert result[0]["ts"] == "1700000000.123456"
        mock_navigate.assert_called_once()
        # Should be called 6 times: 1st (found threads) + 5 times (empty) to satisfy stop condition
        assert mock_extract_summaries.call_count == 6