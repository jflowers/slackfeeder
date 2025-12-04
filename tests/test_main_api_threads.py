import argparse
from unittest.mock import MagicMock, patch, ANY
import pytest
from src.main import main

class TestMainApiThreads:
    @patch('src.main.SlackClient')
    @patch('src.main.GoogleDriveClient')
    @patch('src.main.load_json_file')
    @patch('src.drive_upload.load_json_file')
    @patch('src.drive_upload.load_people_cache')
    @patch('src.main.setup_logging')
    @patch('src.main._validate_and_setup_environment')
    @patch('src.main._setup_output_directory')
    @patch('src.main.save_json_file')  # Mock this to avoid writing files
    def test_orphan_thread_detection_and_fetch(
        self,
        mock_save_json,
        mock_setup_output,
        mock_validate_env,
        mock_setup_logging,
        mock_load_people_cache,
        mock_load_json_drive,
        mock_load_json,
        mock_gdrive_class,
        mock_slack_class
    ):
        # Setup mocks
        mock_slack_client = MagicMock()
        mock_gdrive_client = MagicMock()
        mock_validate_env.return_value = (mock_slack_client, mock_gdrive_client, "folder_id")
        
        mock_setup_output.return_value = "/tmp/fake_output"

        # Mock channels.json - patch src.main.load_json_file which is used in main()
        mock_load_json.return_value = {
            "channels": [{"id": "C1234567890", "name": "test-channel", "export": True}]
        }
        
        # Mock load_people_cache to return empty cache
        mock_load_people_cache.return_value = ({}, set(), set(), None)

        # Mock channel history: Contains a reply but NO root message
        # Thread root: 1000.0
        # Reply: 2000.0 (thread_ts=1000.0)
        mock_slack_client.fetch_channel_history.return_value = [
            {"ts": "2000.0", "thread_ts": "1000.0", "user": "U2", "text": "Reply without root"}
        ]

        # Mock thread history fetch
        # Should return the root + the reply (or more)
        mock_slack_client.fetch_thread_history.return_value = [
            {"ts": "1000.0", "text": "Root message"},
            {"ts": "2000.0", "thread_ts": "1000.0", "text": "Reply without root"}
        ]

        # Prepare arguments
        args = argparse.Namespace(
            make_ref_files=False,
            export_history=True,
            upload_to_drive=False,
            setup_drive_auth=False,
            start_date="2025-01-01",
            end_date="2025-01-02",
            bulk_export=False,
            browser_export_dm=False
        )

        # Run main
        with patch('src.main.os.path.exists', return_value=True), \
             patch('src.main.open', new_callable=MagicMock) as mock_open, \
             patch('src.main.os.fsync'), \
             patch('src.main.os.path.getsize', return_value=100):
             
            main(args)

        # Verification
        
        # 1. fetch_channel_history should be called
        mock_slack_client.fetch_channel_history.assert_called_once()

        # 2. fetch_thread_history should be called for the orphan thread '1000.0'
        mock_slack_client.fetch_thread_history.assert_called_once_with("C1234567890", "1000.0")

        # 3. Verify that the output file content includes the root message
        # Access the file handle returned by the context manager
        file_handle = mock_open.return_value.__enter__.return_value
        
        written_content = ""
        for call in file_handle.write.mock_calls:
            if call.args:
                written_content += call.args[0]
        
        assert "Root message" in written_content
        assert "Reply without root" in written_content
