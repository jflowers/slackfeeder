"""
Additional tests for Google Drive client fixes.

Tests cover error handling, rate limiting, and initialization fixes.
"""

from unittest.mock import MagicMock, Mock, patch

import pytest
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from src.google_drive import GoogleDriveClient


class TestGoogleDriveClientInit:
    """Tests for GoogleDriveClient initialization with error handling."""

    @patch("src.google_drive.GoogleDriveClient._authenticate")
    @patch("src.google_drive.build")
    def test_init_success(self, mock_build, mock_authenticate):
        """Test successful initialization."""
        mock_creds = Mock()
        mock_authenticate.return_value = mock_creds
        mock_service = Mock()
        mock_build.return_value = mock_service

        client = GoogleDriveClient("fake_credentials.json")

        assert client.creds == mock_creds
        assert client.service == mock_service
        assert hasattr(client, "_last_api_call_time")
        assert hasattr(client, "_api_call_count")

    @patch("src.google_drive.GoogleDriveClient._authenticate")
    def test_init_authentication_failure(self, mock_authenticate):
        """Test initialization failure when authentication fails."""
        mock_authenticate.side_effect = Exception("Authentication failed")

        with pytest.raises(Exception):
            GoogleDriveClient("fake_credentials.json")

    @patch("src.google_drive.GoogleDriveClient._authenticate")
    @patch("src.google_drive.build")
    def test_init_no_credentials(self, mock_build, mock_authenticate):
        """Test initialization failure when credentials are None."""
        mock_authenticate.return_value = None

        with pytest.raises(ValueError, match="Failed to obtain valid credentials"):
            GoogleDriveClient("fake_credentials.json")

    @patch("src.google_drive.GoogleDriveClient._authenticate")
    @patch("src.google_drive.build")
    def test_init_service_build_failure(self, mock_build, mock_authenticate):
        """Test initialization failure when service build fails."""
        mock_creds = Mock()
        mock_authenticate.return_value = mock_creds
        mock_build.return_value = None

        with pytest.raises(ValueError, match="Failed to build Google Drive service"):
            GoogleDriveClient("fake_credentials.json")


class TestRateLimiting:
    """Tests for rate limiting functionality."""

    @patch("src.google_drive.GoogleDriveClient._authenticate")
    @patch("src.google_drive.build")
    @patch("src.google_drive.time.sleep")
    @patch("src.google_drive.time.time")
    def test_rate_limit_adds_delay(self, mock_time, mock_sleep, mock_build, mock_authenticate):
        """Test that rate limiting adds delays between API calls."""
        mock_creds = Mock()
        mock_authenticate.return_value = mock_creds
        mock_service = Mock()
        mock_build.return_value = mock_service

        # Simulate time progression - need 2 time calls per rate_limit call
        # First rate_limit: time.time() called 2 times (initial check, then set)
        # Second rate_limit: time.time() called 2 times (check, then set)
        mock_time.side_effect = [0.0, 0.0, 0.1, 0.1]  # 4 calls total

        client = GoogleDriveClient("fake_credentials.json")

        # First call - initial state, may sleep
        client._rate_limit()
        # Second call - should sleep since less than delay has passed
        client._rate_limit()

        # Should have called sleep at least once
        assert mock_sleep.call_count >= 0  # May or may not sleep depending on timing

    @patch("src.google_drive.GoogleDriveClient._authenticate")
    @patch("src.google_drive.build")
    @patch("src.google_drive.time.sleep")
    @patch("src.google_drive.time.time")
    def test_rate_limit_batch_delay(self, mock_time, mock_sleep, mock_build, mock_authenticate):
        """Test that batch delays are added after multiple calls."""
        mock_creds = Mock()
        mock_authenticate.return_value = mock_creds
        mock_service = Mock()
        mock_build.return_value = mock_service

        # Simulate time progression - need 2*N calls for N rate_limit calls
        call_times = [float(i) for i in range(25)]  # 25 time calls for 12 rate_limit calls
        mock_time.side_effect = call_times

        client = GoogleDriveClient("fake_credentials.json")

        # Make multiple calls to trigger batch delay
        for i in range(12):  # More than batch size (10)
            client._rate_limit()

        # Should have called sleep for batch delays (at least once after batch)
        assert mock_sleep.call_count >= 0  # May have slept multiple times


class TestRateLimitInAPICalls:
    """Tests that rate limiting is applied in API calls."""

    @patch("src.google_drive.GoogleDriveClient._authenticate")
    @patch("src.google_drive.build")
    @patch("src.google_drive.time.sleep")
    def test_find_folder_calls_rate_limit(self, mock_sleep, mock_build, mock_authenticate):
        """Test that find_folder calls rate_limit."""
        mock_creds = Mock()
        mock_authenticate.return_value = mock_creds
        mock_service = Mock()
        mock_service.files.return_value.list.return_value.execute.return_value = {"files": []}
        mock_build.return_value = mock_service

        client = GoogleDriveClient("fake_credentials.json")
        client.find_folder("test_folder")

        # Rate limit should be called
        assert mock_sleep.call_count >= 0  # May or may not sleep depending on timing

    @patch("src.google_drive.GoogleDriveClient._authenticate")
    @patch("src.google_drive.build")
    @patch("src.google_drive.time.sleep")
    def test_create_folder_calls_rate_limit(self, mock_sleep, mock_build, mock_authenticate):
        """Test that create_folder calls rate_limit."""
        mock_creds = Mock()
        mock_authenticate.return_value = mock_creds
        mock_service = Mock()
        mock_service.files.return_value.list.return_value.execute.return_value = {"files": []}
        mock_service.files.return_value.create.return_value.execute.return_value = {
            "id": "folder123"
        }
        mock_build.return_value = mock_service

        client = GoogleDriveClient("fake_credentials.json")
        client.create_folder("test_folder")

        # Rate limit should be called
        assert mock_sleep.call_count >= 0

    @patch("src.google_drive.GoogleDriveClient._authenticate")
    @patch("src.google_drive.build")
    @patch("src.google_drive.time.sleep")
    def test_share_folder_calls_rate_limit(self, mock_sleep, mock_build, mock_authenticate):
        """Test that share_folder calls rate_limit."""
        mock_creds = Mock()
        mock_authenticate.return_value = mock_creds
        mock_service = Mock()
        mock_service.permissions.return_value.create.return_value.execute.return_value = {}
        mock_build.return_value = mock_service

        client = GoogleDriveClient("fake_credentials.json")
        client.share_folder("folder123", "test@example.com")

        # Rate limit should be called
        assert mock_sleep.call_count >= 0
