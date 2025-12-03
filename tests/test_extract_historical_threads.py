import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

# Import the module under test
from scripts.extract_historical_threads import extract_historical_threads_via_search

# Mock data
MOCK_SEARCH_RESULTS_JS_OUTPUT = {
    "results": [
        {
            "thread_ts": "1700000000.123456",
            "conversation_id": "C1",
            "click_element_uid": "thread_click_1",
        },
    ]
}

MOCK_MESSAGES = [
    {"ts": "1700000000.123456", "user": "U_TEST", "text": "Test message"}
]

class TestExtractHistoricalThreads:
    
    def setup_method(self):
        self.mock_evaluate_script = MagicMock()
        self.mock_click = MagicMock()
        self.mock_press_key = MagicMock()
        self.mock_fill = MagicMock()

    @patch('scripts.extract_historical_threads.time.sleep', return_value=None)
    @patch('scripts.extract_historical_threads.expand_and_extract_thread_replies')
    def test_extract_historical_threads_via_search(self, mock_expand, mock_sleep):
        """Test the search-based extraction orchestration."""
        # Setup mocks
        # 1. Search results found (Page 1)
        # 2. Search results empty (Page 2) -> Stop
        self.mock_evaluate_script.side_effect = [
            {"result": MOCK_SEARCH_RESULTS_JS_OUTPUT}, # Page 1 results
            {"result": {"uid": "next_page_uid"}},      # Find Next page button
            {"result": {"results": []}},               # Page 2 results (empty)
        ]
        
        mock_expand.return_value = MOCK_MESSAGES

        export_date_range = (datetime.min.replace(tzinfo=timezone.utc), datetime.max.replace(tzinfo=timezone.utc))

        result = extract_historical_threads_via_search(
            self.mock_evaluate_script,
            self.mock_click,
            self.mock_press_key,
            self.mock_fill,
            "in:test",
            export_date_range
        )

        assert len(result) == 1
        assert result[0]["text"] == "Test message"
        
        # Should click next page
        self.mock_click.assert_any_call(uid="next_page_uid")
        
        # Should call expand logic for the found thread
        mock_expand.assert_called_once()
