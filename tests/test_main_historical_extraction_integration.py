import argparse
import sys
import json
import os
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone

import pytest

# Import main directly to test its argument parsing and function calls
import src.main as main

class TestMainHistoricalExtraction:

    @patch('src.main._validate_and_setup_environment', return_value=(MagicMock(), MagicMock(), None))
    @patch('src.main.load_browser_export_config', return_value={ "browser-export": [{"id": "C1", "name": "proj-complytime", "export": True}]})
    @patch('src.main.select_conversation_from_sidebar')
    @patch('src.browser_response_processor.BrowserResponseProcessor')
    @patch('src.main.upload_messages_to_drive', return_value={})
    @patch('scripts.extract_historical_threads.extract_historical_threads_via_search')
    @patch('src.main.setup_logging') # Mock logging setup to prevent file output during tests
    @patch('src.main.logger') # Mock the logger object itself
    @patch('src.main.GoogleDriveClient') # Mock GoogleDriveClient
    @patch('src.main.SlackClient') # Mock SlackClient
    @patch('os.path.exists', return_value=True)
    @patch('os.path.isfile', return_value=True)
    @patch('os.access', return_value=True)
    @patch.dict(os.environ, {
        'SLACK_BOT_TOKEN': 'xoxb-dummy-slack-token',
        'GOOGLE_DRIVE_CREDENTIALS_FILE': '/tmp/dummy_credentials.json'
    })
    def test_historical_thread_extraction_flow(
        self,
        mock_access,
        mock_isfile,
        mock_exists,
        mock_slack_client,
        mock_google_drive_client,
        mock_logger,
        mock_setup_logging,
        mock_extract_historical_threads_via_search,
        mock_upload_messages_to_drive,
        mock_browser_response_processor,
        mock_select_conversation,
        mock_load_browser_export_config,
        mock_validate_and_setup_environment
    ):
        """Test the end-to-end flow of historical thread extraction via main.py."""

        # Create mocks for MCP functions that main.py would pass
        mock_mcp_evaluate_script = MagicMock()
        mock_mcp_click = MagicMock()
        mock_mcp_press_key = MagicMock()
        mock_mcp_fill = MagicMock()

        # Mock BrowserResponseProcessor instance
        mock_processor_instance = MagicMock()
        mock_processor_instance._filter_by_conversation_participants.return_value = []
        mock_browser_response_processor.return_value = mock_processor_instance

        # Mock GoogleDriveClient instance methods needed later
        mock_drive_instance = MagicMock()
        mock_drive_instance.create_folder.return_value = "mock_folder_id"
        # Mock get_latest_export_timestamp to return None (no previous export)
        mock_drive_instance.get_latest_export_timestamp.return_value = None
        mock_google_drive_client.return_value = mock_drive_instance

        # Mock stdin to prevent sys.exit(1) due to no piped input
        mock_stdin_isatty = patch('sys.stdin.isatty', return_value=False)
        mock_stdin_read = patch('sys.stdin.read', return_value=json.dumps({"messages": []}))

        with mock_stdin_isatty, mock_stdin_read:

            # Simulate CLI arguments and create an argparse.Namespace object
            args_namespace = argparse.Namespace(
                browser_export_dm=True,
                upload_to_drive=True,
                browser_export_config="config/browser-export.json",
                browser_conversation_name="proj-complytime",
                start_date="2025-01-01",
                end_date="2025-01-31",
                extract_historical_threads=True,
                extract_active_threads=False,
                search_query="in:#proj-complytime after:2025-01-01 before:2025-01-31 is:thread",
                # Include all other args that main.py expects, with their default values
                select_conversation=True, 
                make_ref_files=False,
                export_history=False,
                setup_drive_auth=False,
                browser_response_dir="browser_exports",
                browser_output_dir="slack_exports",
                browser_conversation_id=None, # Will be populated from config in main.py
                bulk_export=False,
            )

            # Use a timestamp that falls within the test date range (2025-01-01 to 2025-01-31)
            # 2025-01-15 12:00:00 UTC = 1736942400.0
            mock_extract_historical_threads_via_search.return_value = [
                {"ts": "1736942400.000001", "text": "Historical thread message", "user": "test_user"}
            ]

            with patch.object(sys, 'argv', ['src/main.py'] + []): # argv is patched but not used for args parsing here
                main.main(
                    args=args_namespace,
                    mcp_evaluate_script=mock_mcp_evaluate_script,
                    mcp_click=mock_mcp_click,
                    mcp_press_key=mock_mcp_press_key,
                    mcp_fill=mock_mcp_fill
                )
                
                # Assert that the historical extraction function was called
                mock_extract_historical_threads_via_search.assert_called_once_with(
                    mcp_evaluate_script=mock_mcp_evaluate_script,
                    mcp_click=mock_mcp_click,
                    mcp_press_key=mock_mcp_press_key,
                    mcp_fill=mock_mcp_fill,
                    search_query="in:#proj-complytime after:2025-01-01 before:2025-01-31 is:thread",
                    export_date_range=(
                        datetime(2025, 1, 1, tzinfo=timezone.utc),
                        datetime(2025, 1, 31, tzinfo=timezone.utc)
                    )
                )
                
                mock_logger.info.assert_any_call("Attempting to extract historical threads via search.")

    @patch('src.main._validate_and_setup_environment', return_value=(MagicMock(), MagicMock(), None))
    @patch('src.main.load_browser_export_config', return_value={ "browser-export": [{"id": "C1", "name": "my test channel", "export": True}]})
    @patch('src.main.select_conversation_from_sidebar')
    @patch('src.browser_response_processor.BrowserResponseProcessor')
    @patch('src.main.upload_messages_to_drive', return_value={})
    @patch('scripts.extract_historical_threads.extract_historical_threads_via_search')
    @patch('src.main.setup_logging') # Mock logging setup to prevent file output during tests
    @patch('src.main.logger') # Mock the logger object itself
    @patch('src.main.GoogleDriveClient') # Mock GoogleDriveClient
    @patch('src.main.SlackClient') # Mock SlackClient
    @patch('os.path.exists', return_value=True)
    @patch('os.path.isfile', return_value=True)
    @patch('os.access', return_value=True)
    @patch.dict(os.environ, {
        'SLACK_BOT_TOKEN': 'xoxb-dummy-slack-token',
        'GOOGLE_DRIVE_CREDENTIALS_FILE': '/tmp/dummy_credentials.json'
    })
    def test_historical_thread_extraction_query_construction(
        self,
        mock_access,
        mock_isfile,
        mock_exists,
        mock_slack_client,
        mock_google_drive_client,
        mock_logger,
        mock_setup_logging,
        mock_extract_historical_threads_via_search,
        mock_upload_messages_to_drive,
        mock_browser_response_processor,
        mock_select_conversation,
        mock_load_browser_export_config,
        mock_validate_and_setup_environment
    ):
        """Test that search query is correctly constructed when --search-query is not provided."""

        mock_mcp_evaluate_script = MagicMock()
        mock_mcp_click = MagicMock()
        mock_mcp_press_key = MagicMock()
        mock_mcp_fill = MagicMock()

        # Mock BrowserResponseProcessor instance
        mock_processor_instance = MagicMock()
        mock_processor_instance._filter_by_conversation_participants.return_value = []
        mock_browser_response_processor.return_value = mock_processor_instance

        # Mock GoogleDriveClient instance methods needed later
        mock_drive_instance = MagicMock()
        mock_drive_instance.create_folder.return_value = "mock_folder_id"
        # Mock get_latest_export_timestamp to return None (no previous export)
        mock_drive_instance.get_latest_export_timestamp.return_value = None
        mock_google_drive_client.return_value = mock_drive_instance

        # Mock stdin to prevent sys.exit(1) due to no piped input
        mock_stdin_isatty = patch('sys.stdin.isatty', return_value=False)
        mock_stdin_read = patch('sys.stdin.read', return_value=json.dumps({"messages": []}))

        with mock_stdin_isatty, mock_stdin_read:

            # Simulate CLI arguments and create an argparse.Namespace object without --search-query
            args_namespace = argparse.Namespace(
                browser_export_dm=True,
                upload_to_drive=True,
                browser_export_config="config/browser-export.json",
                browser_conversation_name="my test channel",
                start_date="2025-03-01",
                end_date="2025-03-05",
                extract_historical_threads=True,
                extract_active_threads=False,
                search_query=None, # Explicitly not provided
                select_conversation=True, 
                make_ref_files=False,
                export_history=False,
                setup_drive_auth=False,
                browser_response_dir="browser_exports",
                browser_output_dir="slack_exports",
                browser_conversation_id=None, 
                bulk_export=False,
            )

            # Use a timestamp that falls within the test date range (2025-03-01 to 2025-03-05)
            # 2025-03-03 12:00:00 UTC = 1741003200.0
            mock_extract_historical_threads_via_search.return_value = [
                {"ts": "1741003200.000001", "text": "Historical thread message", "user": "test_user"}
            ]

            with patch.object(sys, 'argv', ['src/main.py'] + []):
                main.main(
                    args=args_namespace,
                    mcp_evaluate_script=mock_mcp_evaluate_script,
                    mcp_click=mock_mcp_click,
                    mcp_press_key=mock_mcp_press_key,
                    mcp_fill=mock_mcp_fill
                )
                
                # Assert that the historical extraction function was called
                expected_query = 'in:"my test channel" after:2025-03-01 before:2025-03-05 is:thread'
                mock_extract_historical_threads_via_search.assert_called_once_with(
                    mcp_evaluate_script=mock_mcp_evaluate_script,
                    mcp_click=mock_mcp_click,
                    mcp_press_key=mock_mcp_press_key,
                    mcp_fill=mock_mcp_fill,
                    search_query=expected_query,
                    export_date_range=(
                        datetime(2025, 3, 1, tzinfo=timezone.utc),
                        datetime(2025, 3, 5, tzinfo=timezone.utc)
                    )
                )