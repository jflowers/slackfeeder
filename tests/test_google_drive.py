"""
Unit tests for Google Drive client.

Tests use mocks to avoid requiring actual Google Drive API credentials.
"""
import pytest
from unittest.mock import Mock, patch, MagicMock, mock_open
from googleapiclient.errors import HttpError
from src.google_drive import GoogleDriveClient


class TestEscapeDriveQueryString:
    """Tests for _escape_drive_query_string method."""
    
    def test_escape_backslashes(self):
        client = GoogleDriveClient.__new__(GoogleDriveClient)
        assert client._escape_drive_query_string("path\\to\\file") == "path\\\\to\\\\file"
    
    def test_escape_single_quotes(self):
        client = GoogleDriveClient.__new__(GoogleDriveClient)
        assert client._escape_drive_query_string("O'Reilly") == "O\\'Reilly"
    
    def test_escape_double_quotes(self):
        client = GoogleDriveClient.__new__(GoogleDriveClient)
        assert client._escape_drive_query_string('file"name"') == 'file\\"name\\"'
    
    def test_empty_string(self):
        client = GoogleDriveClient.__new__(GoogleDriveClient)
        assert client._escape_drive_query_string("") == ""
    
    def test_none(self):
        client = GoogleDriveClient.__new__(GoogleDriveClient)
        assert client._escape_drive_query_string(None) == ""
    
    def test_complex_escaping(self):
        client = GoogleDriveClient.__new__(GoogleDriveClient)
        result = client._escape_drive_query_string("file'name\"with\\backslashes")
        assert "\\'" in result
        assert '\\"' in result
        assert "\\\\" in result


class TestValidateFolderId:
    """Tests for _validate_folder_id method."""
    
    def test_valid_folder_ids(self):
        client = GoogleDriveClient.__new__(GoogleDriveClient)
        valid_ids = [
            "0B1234567890abcdef",
            "1a2b3c4d5e6f7g8h9i0j",
            "ABCDEFGHIJKLMNOPQRSTUVWXYZ123456789",
        ]
        for folder_id in valid_ids:
            assert client._validate_folder_id(folder_id) is True
    
    def test_invalid_folder_ids(self):
        client = GoogleDriveClient.__new__(GoogleDriveClient)
        invalid_ids = [
            None,
            "",
            "short",  # Too short
            "a" * 60,  # Too long
            "folder/id",  # Contains slash
            "folder id",  # Contains space
            "folder@id",  # Contains @
        ]
        for folder_id in invalid_ids:
            assert client._validate_folder_id(folder_id) is False


class TestFindFolder:
    """Tests for find_folder method."""
    
    @patch('src.google_drive.build')
    def test_find_existing_folder(self, mock_build):
        # Setup mock service with proper chain
        mock_service = Mock()
        mock_list_result = Mock()
        mock_list_result.execute.return_value = {"files": [{"id": "folder123", "name": "Test Folder"}]}
        mock_service.files.return_value.list.return_value = mock_list_result
        mock_build.return_value = mock_service
        
        # Mock authentication
        with patch('src.google_drive.GoogleDriveClient._authenticate', return_value=Mock()):
            client = GoogleDriveClient("fake_credentials.json")
            client.service = mock_service  # Set the service directly
            result = client.find_folder("Test Folder")
            assert result == "folder123"
    
    @patch('src.google_drive.build')
    def test_find_nonexistent_folder(self, mock_build):
        mock_service = Mock()
        mock_list_result = Mock()
        mock_list_result.execute.return_value = {"files": []}
        mock_service.files.return_value.list.return_value = mock_list_result
        mock_build.return_value = mock_service
        
        with patch('src.google_drive.GoogleDriveClient._authenticate', return_value=Mock()):
            client = GoogleDriveClient("fake_credentials.json")
            client.service = mock_service  # Set the service directly
            result = client.find_folder("Nonexistent")
            assert result is None
    
    @patch('src.google_drive.build')
    def test_find_folder_with_invalid_parent_id(self, mock_build):
        mock_service = Mock()
        mock_build.return_value = mock_service
        
        with patch('src.google_drive.GoogleDriveClient._authenticate', return_value=Mock()):
            client = GoogleDriveClient("fake_credentials.json")
            client.service = mock_service  # Set the service directly
            result = client.find_folder("Test Folder", parent_folder_id="invalid!")
            assert result is None
    
    @patch('src.google_drive.build')
    def test_find_folder_handles_http_error(self, mock_build):
        mock_service = Mock()
        mock_error = HttpError(Mock(status=500), b'{}')
        mock_service.files.return_value.list.return_value.execute.side_effect = mock_error
        mock_build.return_value = mock_service
        
        with patch('src.google_drive.GoogleDriveClient._authenticate', return_value=Mock()):
            client = GoogleDriveClient("fake_credentials.json")
            client.service = mock_service  # Set the service directly
            result = client.find_folder("Test Folder")
            assert result is None


class TestCreateFolder:
    """Tests for create_folder method."""
    
    @patch('src.google_drive.build')
    def test_create_new_folder(self, mock_build):
        mock_service = Mock()
        # Mock find_folder to return None (folder doesn't exist)
        mock_list_result = Mock()
        mock_list_result.execute.return_value = {"files": []}
        mock_service.files.return_value.list.return_value = mock_list_result
        
        # Mock create
        mock_create_result = Mock()
        mock_create_result.execute.return_value = {"id": "new_folder123"}
        mock_service.files.return_value.create.return_value = mock_create_result
        
        mock_build.return_value = mock_service
        
        with patch('src.google_drive.GoogleDriveClient._authenticate', return_value=Mock()):
            client = GoogleDriveClient("fake_credentials.json")
            client.service = mock_service  # Set the service directly
            result = client.create_folder("New Folder")
            assert result == "new_folder123"
    
    @patch('src.google_drive.build')
    def test_create_existing_folder_returns_existing_id(self, mock_build):
        mock_service = Mock()
        mock_list_result = Mock()
        mock_list_result.execute.return_value = {"files": [{"id": "existing_folder123"}]}
        mock_service.files.return_value.list.return_value = mock_list_result
        mock_build.return_value = mock_service
        
        with patch('src.google_drive.GoogleDriveClient._authenticate', return_value=Mock()):
            client = GoogleDriveClient("fake_credentials.json")
            client.service = mock_service  # Set the service directly
            result = client.create_folder("Existing Folder")
            assert result == "existing_folder123"
            # Verify create was not called
            mock_service.files.return_value.create.assert_not_called()
    
    @patch('src.google_drive.build')
    def test_create_folder_with_empty_name(self, mock_build):
        mock_service = Mock()
        mock_build.return_value = mock_service
        
        with patch('src.google_drive.GoogleDriveClient._authenticate', return_value=Mock()):
            with patch('src.google_drive.GoogleDriveClient._authenticate', return_value=Mock()):
                with patch('src.google_drive.build', return_value=Mock()):
                    client = GoogleDriveClient("fake_credentials.json")
            result = client.create_folder("")
            assert result is None
    
    @patch('src.google_drive.build')
    def test_create_folder_truncates_long_name(self, mock_build):
        mock_service = Mock()
        # Mock find_folder to return None (folder doesn't exist)
        mock_list_result = Mock()
        mock_list_result.execute.return_value = {"files": []}  # Empty list
        mock_service.files.return_value.list.return_value = mock_list_result
        
        # Mock create result
        mock_create_result = Mock()
        mock_create_result.execute.return_value = {"id": "folder123"}
        mock_service.files.return_value.create.return_value = mock_create_result
        
        mock_build.return_value = mock_service
        
        with patch('src.google_drive.GoogleDriveClient._authenticate', return_value=Mock()):
            client = GoogleDriveClient("fake_credentials.json")
            client.service = mock_service  # Set the service directly
            long_name = "a" * 300
            result = client.create_folder(long_name)
            assert result == "folder123"
            # Verify the name was truncated
            call_args = mock_service.files.return_value.create.call_args
            assert len(call_args[1]['body']['name']) == 255


class TestUploadFile:
    """Tests for upload_file method."""
    
    @patch('src.google_drive.build')
    @patch('src.google_drive.os.path.exists', return_value=True)
    @patch('src.google_drive.os.path.basename', return_value="test.txt")
    @patch('src.google_drive.MediaFileUpload')
    def test_upload_new_file(self, mock_media_upload, mock_basename, mock_exists, mock_build):
        mock_service = Mock()
        mock_create_result = Mock()
        mock_create_result.execute.return_value = {"id": "file123"}
        mock_service.files.return_value.create.return_value = mock_create_result
        
        mock_list_result = Mock()
        mock_list_result.execute.return_value = {"files": []}  # Empty list, not a Mock
        mock_service.files.return_value.list.return_value = mock_list_result
        
        mock_build.return_value = mock_service
        
        with patch('src.google_drive.GoogleDriveClient._authenticate', return_value=Mock()):
            client = GoogleDriveClient("fake_credentials.json")
            client.service = mock_service  # Set the service directly
            # Use a valid folder ID format
            result = client.upload_file("/path/to/test.txt", "0B1234567890abcdef")
            assert result == "file123"
    
    @patch('src.google_drive.build')
    @patch('src.google_drive.os.path.exists', return_value=False)
    def test_upload_nonexistent_file(self, mock_exists, mock_build):
        mock_service = Mock()
        mock_build.return_value = mock_service
        
        with patch('src.google_drive.GoogleDriveClient._authenticate', return_value=Mock()):
            with patch('src.google_drive.GoogleDriveClient._authenticate', return_value=Mock()):
                with patch('src.google_drive.build', return_value=Mock()):
                    client = GoogleDriveClient("fake_credentials.json")
            result = client.upload_file("/nonexistent/file.txt", "folder123")
            assert result is None
    
    @patch('src.google_drive.build')
    @patch('src.google_drive.os.path.exists', return_value=True)
    @patch('src.google_drive.os.path.basename', return_value="test.txt")
    def test_upload_with_invalid_folder_id(self, mock_basename, mock_exists, mock_build):
        mock_service = Mock()
        mock_build.return_value = mock_service
        
        with patch('src.google_drive.GoogleDriveClient._authenticate', return_value=Mock()):
            with patch('src.google_drive.GoogleDriveClient._authenticate', return_value=Mock()):
                with patch('src.google_drive.build', return_value=Mock()):
                    client = GoogleDriveClient("fake_credentials.json")
            result = client.upload_file("/path/to/test.txt", "invalid!")
            assert result is None


class TestShareFolder:
    """Tests for share_folder method."""
    
    @patch('src.google_drive.build')
    def test_share_successfully(self, mock_build):
        mock_service = Mock()
        mock_create_result = Mock()
        mock_create_result.execute.return_value = {"id": "permission123"}
        mock_service.permissions.return_value.create.return_value = mock_create_result
        mock_build.return_value = mock_service
        
        with patch('src.google_drive.GoogleDriveClient._authenticate', return_value=Mock()):
            with patch('src.google_drive.GoogleDriveClient._authenticate', return_value=Mock()):
                with patch('src.google_drive.build', return_value=Mock()):
                    client = GoogleDriveClient("fake_credentials.json")
            # Use a valid folder ID format
            result = client.share_folder("0B1234567890abcdef", "user@example.com")
            assert result is True
    
    @patch('src.google_drive.build')
    def test_share_already_shared(self, mock_build):
        mock_service = Mock()
        mock_create_result = Mock()
        mock_error = HttpError(Mock(status=400), b'already has access')
        mock_create_result.execute.side_effect = mock_error
        mock_service.permissions.return_value.create.return_value = mock_create_result
        mock_build.return_value = mock_service
        
        with patch('src.google_drive.GoogleDriveClient._authenticate', return_value=Mock()):
            with patch('src.google_drive.GoogleDriveClient._authenticate', return_value=Mock()):
                with patch('src.google_drive.build', return_value=Mock()):
                    client = GoogleDriveClient("fake_credentials.json")
            # Use a valid folder ID format
            result = client.share_folder("0B1234567890abcdef", "user@example.com")
            assert result is True  # Already shared is considered success
    
    @patch('src.google_drive.build')
    def test_share_with_invalid_email(self, mock_build):
        mock_service = Mock()
        mock_build.return_value = mock_service
        
        with patch('src.google_drive.GoogleDriveClient._authenticate', return_value=Mock()):
            with patch('src.google_drive.GoogleDriveClient._authenticate', return_value=Mock()):
                with patch('src.google_drive.build', return_value=Mock()):
                    client = GoogleDriveClient("fake_credentials.json")
            result = client.share_folder("folder123", "")
            assert result is False
    
    @patch('src.google_drive.build')
    def test_share_with_invalid_folder_id(self, mock_build):
        mock_service = Mock()
        mock_build.return_value = mock_service
        
        with patch('src.google_drive.GoogleDriveClient._authenticate', return_value=Mock()):
            with patch('src.google_drive.GoogleDriveClient._authenticate', return_value=Mock()):
                with patch('src.google_drive.build', return_value=Mock()):
                    client = GoogleDriveClient("fake_credentials.json")
            result = client.share_folder("invalid!", "user@example.com")
            assert result is False
