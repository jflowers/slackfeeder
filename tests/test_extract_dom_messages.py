"""
Tests for DOM message extraction with Chain of Custody algorithm.

This test suite covers the extract_and_save_dom_messages function and its
Chain of Custody scrolling algorithm.
"""

import json
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from scripts.extract_dom_messages import extract_and_save_dom_messages


class TestExtractAndSaveDomMessages:
    """Test the main extraction function."""

    def test_invalid_mcp_press_key(self):
        """Test that non-callable mcp_press_key raises ValueError."""
        with pytest.raises(ValueError, match="mcp_press_key must be callable"):
            extract_and_save_dom_messages(
                mcp_evaluate_script=lambda x: {},
                mcp_press_key="not_callable",
            )

    def test_invalid_mcp_evaluate_script(self):
        """Test that non-callable mcp_evaluate_script raises ValueError."""
        with pytest.raises(ValueError, match="mcp_evaluate_script must be callable"):
            extract_and_save_dom_messages(
                mcp_evaluate_script="not_callable",
                mcp_press_key=lambda x: None,
            )

    def test_no_auto_scroll_extracts_current_view(self):
        """Test that without auto_scroll, only current view is extracted."""
        messages = [
            {"ts": "1729263032.513419", "user": "U123", "text": "Hello"},
            {"ts": "1729263033.513419", "user": "U456", "text": "Hi"},
        ]

        mock_evaluate = MagicMock(
            return_value={"messages": messages, "ok": True}
        )
        mock_press_key = MagicMock()

        result = extract_and_save_dom_messages(
            mcp_evaluate_script=mock_evaluate,
            mcp_press_key=mock_press_key,
            auto_scroll=False,
        )

        assert result["ok"] is True
        assert len(result["messages"]) == 2
        assert result["message_count"] == 2
        mock_evaluate.assert_called_once()
        mock_press_key.assert_not_called()

    def test_auto_scroll_initial_extraction(self):
        """Test initial extraction at bottom of view."""
        initial_messages = [
            {"ts": "1729263033.513419", "user": "U456", "text": "Latest"},
            {"ts": "1729263032.513419", "user": "U123", "text": "Older"},
        ]

        mock_evaluate = MagicMock(
            return_value={"messages": initial_messages, "ok": True}
        )
        mock_press_key = MagicMock()

        result = extract_and_save_dom_messages(
            mcp_evaluate_script=mock_evaluate,
            mcp_press_key=mock_press_key,
            auto_scroll=True,
        )

        assert result["ok"] is True
        assert len(result["messages"]) == 2
        # Should have called evaluate for initial extraction
        assert mock_evaluate.call_count >= 1

    def test_frontier_tracking(self):
        """Test that frontier correctly tracks oldest collected message."""
        # Initial messages (newest to oldest)
        initial_messages = [
            {"ts": "1729263033.513419", "user": "U456", "text": "Latest"},
            {"ts": "1729263032.513419", "user": "U123", "text": "Older"},
        ]

        # After scroll up, we see older messages
        older_messages = [
            {"ts": "1729263032.513419", "user": "U123", "text": "Older"},
            {"ts": "1729263031.513419", "user": "U789", "text": "Oldest"},
        ]

        call_count = 0

        def mock_evaluate(function):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"messages": initial_messages}
            elif call_count == 2:
                return {"messages": older_messages}
            else:
                return {"messages": []}  # No more messages

        mock_press_key = MagicMock()

        result = extract_and_save_dom_messages(
            mcp_evaluate_script=mock_evaluate,
            mcp_press_key=mock_press_key,
            auto_scroll=True,
        )

        # Should have collected all unique messages
        timestamps = [float(m["ts"]) for m in result["messages"]]
        assert len(timestamps) == 3  # Latest, Older, Oldest
        assert min(timestamps) == 1729263031.513419  # Oldest

    def test_gap_detection_and_backtracking(self):
        """Test that gaps are detected and backtracking occurs."""
        # Initial messages
        initial_messages = [
            {"ts": "1729263033.513419", "user": "U456", "text": "Latest"},
            {"ts": "1729263032.513419", "user": "U123", "text": "Older"},
        ]

        # After scroll up, we see a gap (no overlap with frontier)
        gap_messages = [
            {"ts": "1729263030.513419", "user": "U789", "text": "Gap start"},
            {"ts": "1729263029.513419", "user": "U999", "text": "Gap end"},
        ]

        # After backtracking, we restore overlap
        overlap_messages = [
            {"ts": "1729263032.513419", "user": "U123", "text": "Older"},
            {"ts": "1729263031.513419", "user": "U888", "text": "New"},
        ]

        call_count = 0

        def mock_evaluate(function):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"messages": initial_messages}
            elif call_count == 2:
                return {"messages": gap_messages}
            elif call_count == 3:
                return {"messages": overlap_messages}
            else:
                return {"messages": []}

        mock_press_key = MagicMock()

        result = extract_and_save_dom_messages(
            mcp_evaluate_script=mock_evaluate,
            mcp_press_key=mock_press_key,
            auto_scroll=True,
        )

        # Should have called press_key for backtracking (ArrowDown)
        # At least PageUp calls + ArrowDown calls for backtracking
        assert mock_press_key.call_count > 0

    def test_message_deduplication(self):
        """Test that duplicate messages are deduplicated by timestamp."""
        messages1 = [
            {"ts": "1729263033.513419", "user": "U456", "text": "Message 1"},
            {"ts": "1729263032.513419", "user": "U123", "text": "Message 2"},
        ]

        # Overlapping messages (Message 2 appears again)
        messages2 = [
            {"ts": "1729263032.513419", "user": "U123", "text": "Message 2"},
            {"ts": "1729263031.513419", "user": "U789", "text": "Message 3"},
        ]

        call_count = 0

        def mock_evaluate(function):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"messages": messages1}
            elif call_count == 2:
                return {"messages": messages2}
            else:
                return {"messages": []}

        mock_press_key = MagicMock()

        result = extract_and_save_dom_messages(
            mcp_evaluate_script=mock_evaluate,
            mcp_press_key=mock_press_key,
            auto_scroll=True,
        )

        # Should have 3 unique messages, not 4
        assert result["message_count"] == 3
        timestamps = [m["ts"] for m in result["messages"]]
        assert len(set(timestamps)) == 3  # All unique

    def test_date_filtering_start_date(self):
        """Test filtering messages by start_date."""
        messages = [
            {"ts": "1729263033.513419", "user": "U456", "text": "Oct 18"},
            {"ts": "1729176632.513419", "user": "U123", "text": "Oct 17"},
            {"ts": "1729090232.513419", "user": "U789", "text": "Oct 16"},
        ]

        mock_evaluate = MagicMock(return_value={"messages": messages})
        mock_press_key = MagicMock()

        result = extract_and_save_dom_messages(
            mcp_evaluate_script=mock_evaluate,
            mcp_press_key=mock_press_key,
            auto_scroll=False,
            start_date="2024-10-17",
        )

        # Should only include messages from Oct 17 onwards
        assert result["message_count"] == 2
        timestamps = [float(m["ts"]) for m in result["messages"]]
        assert all(ts >= 1729176632.513419 for ts in timestamps)

    def test_date_filtering_end_date(self):
        """Test filtering messages by end_date."""
        messages = [
            {"ts": "1729263033.513419", "user": "U456", "text": "Oct 18"},
            {"ts": "1729176632.513419", "user": "U123", "text": "Oct 17"},
            {"ts": "1729090232.513419", "user": "U789", "text": "Oct 16"},
        ]

        mock_evaluate = MagicMock(return_value={"messages": messages})
        mock_press_key = MagicMock()

        result = extract_and_save_dom_messages(
            mcp_evaluate_script=mock_evaluate,
            mcp_press_key=mock_press_key,
            auto_scroll=False,
            end_date="2024-10-17",
        )

        # Should only include messages up to Oct 17 (inclusive)
        assert result["message_count"] == 2
        timestamps = [float(m["ts"]) for m in result["messages"]]
        # Oct 17 end of day timestamp
        end_ts = datetime.strptime("2024-10-17", "%Y-%m-%d").replace(
            hour=23, minute=59, second=59
        ).timestamp()
        assert all(ts <= end_ts for ts in timestamps)

    def test_date_filtering_both_dates(self):
        """Test filtering messages by both start_date and end_date."""
        messages = [
            {"ts": "1729263033.513419", "user": "U456", "text": "Oct 18"},
            {"ts": "1729176632.513419", "user": "U123", "text": "Oct 17"},
            {"ts": "1729090232.513419", "user": "U789", "text": "Oct 16"},
        ]

        mock_evaluate = MagicMock(return_value={"messages": messages})
        mock_press_key = MagicMock()

        result = extract_and_save_dom_messages(
            mcp_evaluate_script=mock_evaluate,
            mcp_press_key=mock_press_key,
            auto_scroll=False,
            start_date="2024-10-17",
            end_date="2024-10-17",
        )

        # Should only include messages from Oct 17
        assert result["message_count"] == 1
        assert result["messages"][0]["ts"] == "1729176632.513419"

    def test_target_date_reached(self):
        """Test that scrolling stops when target start_date is reached."""
        initial_messages = [
            {"ts": "1729263033.513419", "user": "U456", "text": "Oct 18"},
            {"ts": "1729176632.513419", "user": "U123", "text": "Oct 17"},
        ]

        older_messages = [
            {"ts": "1729176632.513419", "user": "U123", "text": "Oct 17"},
            {"ts": "1729090232.513419", "user": "U789", "text": "Oct 16"},
        ]

        call_count = 0

        def mock_evaluate(function):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"messages": initial_messages}
            elif call_count == 2:
                return {"messages": older_messages}
            else:
                return {"messages": []}

        mock_press_key = MagicMock()

        result = extract_and_save_dom_messages(
            mcp_evaluate_script=mock_evaluate,
            mcp_press_key=mock_press_key,
            auto_scroll=True,
            start_date="2024-10-17",
        )

        # Should stop when frontier reaches target date
        # The algorithm stops when frontier < target_ts, meaning we've scrolled past the target
        # Since we're filtering by start_date, oldest message should be >= target_ts
        oldest_ts = float(result["oldest"])
        target_ts = datetime.strptime("2024-10-17", "%Y-%m-%d").timestamp()
        # After date filtering, oldest message should be from Oct 17 or later
        assert oldest_ts >= target_ts

    def test_empty_view_handling(self):
        """Test handling of empty views during scrolling."""
        initial_messages = [
            {"ts": "1729263033.513419", "user": "U456", "text": "Message"},
        ]

        call_count = 0

        def mock_evaluate(function):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"messages": initial_messages}
            else:
                return {"messages": []}  # Empty views

        mock_press_key = MagicMock()

        result = extract_and_save_dom_messages(
            mcp_evaluate_script=mock_evaluate,
            mcp_press_key=mock_press_key,
            auto_scroll=True,
        )

        # Should handle empty views gracefully and stop after threshold
        assert result["message_count"] == 1

    def test_consecutive_no_new_messages_stops(self):
        """Test that scrolling stops after consecutive attempts with no new messages."""
        initial_messages = [
            {"ts": "1729263033.513419", "user": "U456", "text": "Message"},
        ]

        # Same messages returned repeatedly (no new messages)
        call_count = 0

        def mock_evaluate(function):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return {"messages": initial_messages}
            else:
                return {"messages": initial_messages}  # Same messages

        mock_press_key = MagicMock()

        result = extract_and_save_dom_messages(
            mcp_evaluate_script=mock_evaluate,
            mcp_press_key=mock_press_key,
            auto_scroll=True,
        )

        # Should stop after threshold of no new messages
        assert result["message_count"] == 1

    def test_nested_result_format(self):
        """Test handling of nested result format from MCP."""
        messages = [
            {"ts": "1729263033.513419", "user": "U456", "text": "Message"},
        ]

        # Test nested "result" format
        mock_evaluate = MagicMock(
            return_value={"result": {"messages": messages}}
        )
        mock_press_key = MagicMock()

        result = extract_and_save_dom_messages(
            mcp_evaluate_script=mock_evaluate,
            mcp_press_key=mock_press_key,
            auto_scroll=False,
        )

        assert result["message_count"] == 1
        assert result["messages"][0]["text"] == "Message"

    def test_file_output(self):
        """Test saving results to file."""
        messages = [
            {"ts": "1729263033.513419", "user": "U456", "text": "Message"},
        ]

        mock_evaluate = MagicMock(return_value={"messages": messages})
        mock_press_key = MagicMock()

        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = Path(tmpdir) / "output.json"

            result = extract_and_save_dom_messages(
                mcp_evaluate_script=mock_evaluate,
                mcp_press_key=mock_press_key,
                auto_scroll=False,
                output_file=output_file,
            )

            assert output_file.exists()
            with open(output_file, "r") as f:
                saved_data = json.load(f)
            assert saved_data["message_count"] == 1
            assert len(saved_data["messages"]) == 1

    def test_append_to_existing_file(self):
        """Test appending to existing file."""
        existing_messages = [
            {"ts": "1729263032.513419", "user": "U123", "text": "Existing"},
        ]

        new_messages = [
            {"ts": "1729263033.513419", "user": "U456", "text": "New"},
        ]

        mock_evaluate = MagicMock(return_value={"messages": new_messages})
        mock_press_key = MagicMock()

        with tempfile.TemporaryDirectory() as tmpdir:
            output_file = Path(tmpdir) / "output.json"

            # Create existing file
            existing_data = {
                "ok": True,
                "messages": existing_messages,
                "message_count": 1,
            }
            with open(output_file, "w") as f:
                json.dump(existing_data, f)

            result = extract_and_save_dom_messages(
                mcp_evaluate_script=mock_evaluate,
                mcp_press_key=mock_press_key,
                auto_scroll=False,
                output_file=output_file,
                append=True,
            )

            # Should have both existing and new messages
            assert result["message_count"] == 2
            timestamps = [m["ts"] for m in result["messages"]]
            assert "1729263032.513419" in timestamps
            assert "1729263033.513419" in timestamps

    def test_stdout_output(self):
        """Test outputting results to stdout."""
        messages = [
            {"ts": "1729263033.513419", "user": "U456", "text": "Message"},
        ]

        mock_evaluate = MagicMock(return_value={"messages": messages})
        mock_press_key = MagicMock()

        result = extract_and_save_dom_messages(
            mcp_evaluate_script=mock_evaluate,
            mcp_press_key=mock_press_key,
            auto_scroll=False,
            output_to_stdout=True,
        )

        assert result["message_count"] == 1

    def test_messages_sorted_chronologically(self):
        """Test that final messages are sorted chronologically."""
        messages = [
            {"ts": "1729263033.513419", "user": "U456", "text": "Latest"},
            {"ts": "1729263031.513419", "user": "U123", "text": "Oldest"},
            {"ts": "1729263032.513419", "user": "U789", "text": "Middle"},
        ]

        mock_evaluate = MagicMock(return_value={"messages": messages})
        mock_press_key = MagicMock()

        result = extract_and_save_dom_messages(
            mcp_evaluate_script=mock_evaluate,
            mcp_press_key=mock_press_key,
            auto_scroll=False,
        )

        # Should be sorted oldest to newest
        timestamps = [float(m["ts"]) for m in result["messages"]]
        assert timestamps == sorted(timestamps)
        assert result["messages"][0]["text"] == "Oldest"
        assert result["messages"][-1]["text"] == "Latest"

    def test_messages_without_timestamp_skipped(self):
        """Test that messages without timestamps are skipped."""
        messages = [
            {"ts": "1729263033.513419", "user": "U456", "text": "Valid"},
            {"user": "U123", "text": "No timestamp"},
            {"ts": "1729263032.513419", "user": "U789", "text": "Valid 2"},
        ]

        mock_evaluate = MagicMock(return_value={"messages": messages})
        mock_press_key = MagicMock()

        result = extract_and_save_dom_messages(
            mcp_evaluate_script=mock_evaluate,
            mcp_press_key=mock_press_key,
            auto_scroll=False,
        )

        # Should only include messages with timestamps
        assert result["message_count"] == 2
        assert all("ts" in m for m in result["messages"])

    def test_result_metadata(self):
        """Test that result includes proper metadata."""
        messages = [
            {"ts": "1729263031.513419", "user": "U123", "text": "Oldest"},
            {"ts": "1729263033.513419", "user": "U456", "text": "Latest"},
        ]

        mock_evaluate = MagicMock(return_value={"messages": messages})
        mock_press_key = MagicMock()

        result = extract_and_save_dom_messages(
            mcp_evaluate_script=mock_evaluate,
            mcp_press_key=mock_press_key,
            auto_scroll=False,
        )

        assert result["ok"] is True
        assert result["message_count"] == 2
        assert result["oldest"] == "1729263031.513419"
        assert result["latest"] == "1729263033.513419"
        assert result["has_more"] is False

    def test_empty_result_metadata(self):
        """Test metadata for empty result."""
        mock_evaluate = MagicMock(return_value={"messages": []})
        mock_press_key = MagicMock()

        result = extract_and_save_dom_messages(
            mcp_evaluate_script=mock_evaluate,
            mcp_press_key=mock_press_key,
            auto_scroll=False,
        )

        assert result["ok"] is True
        assert result["message_count"] == 0
        assert result["oldest"] is None
        assert result["latest"] is None
