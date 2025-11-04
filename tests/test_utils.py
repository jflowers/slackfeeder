"""
Unit tests for slackfeeder utilities.

Tests cover validation, sanitization, and utility functions.
"""
import pytest
import json
import tempfile
import os
from datetime import datetime, timezone
from src.utils import (
    sanitize_filename,
    sanitize_folder_name,
    validate_email,
    validate_channel_id,
    validate_channels_json,
    validate_people_json,
    format_timestamp,
    convert_date_to_timestamp,
    load_json_file,
    save_json_file,
)


class TestSanitizeFilename:
    """Tests for sanitize_filename function."""
    
    def test_normal_filename(self):
        assert sanitize_filename("test_file.txt") == "test_file.txt"
    
    def test_with_path_separators(self):
        assert sanitize_filename("path/to/file") == "path_to_file"
        assert sanitize_filename("path\\to\\file") == "path_to_file"
    
    def test_with_parent_directory(self):
        assert sanitize_filename("../../etc/passwd") == "____etc_passwd"
    
    def test_with_dangerous_characters(self):
        assert sanitize_filename("file<>:\"|?*.txt") == "file_______.txt"
    
    def test_empty_string(self):
        assert sanitize_filename("") == "unnamed"
    
    def test_none(self):
        assert sanitize_filename(None) == "unnamed"
    
    def test_leading_trailing_spaces(self):
        assert sanitize_filename("  file.txt  ") == "file.txt"
        # Dots with ".." get replaced with "_" before stripping, so result is "_.file.txt_"
        assert sanitize_filename("...file.txt...") == "_.file.txt_"
    
    def test_long_filename(self):
        long_name = "a" * 300
        result = sanitize_filename(long_name)
        assert len(result) == 200
        assert result == "a" * 200


class TestSanitizeFolderName:
    """Tests for sanitize_folder_name function."""
    
    def test_normal_folder_name(self):
        assert sanitize_folder_name("My Folder") == "My Folder"
    
    def test_with_invalid_characters(self):
        assert sanitize_folder_name("folder/name") == "folder_name"
        assert sanitize_folder_name("folder\\name") == "folder_name"
        assert sanitize_folder_name("folder<name>") == "folder_name_"
        assert sanitize_folder_name('folder"name"') == "folder_name_"
    
    def test_empty_string(self):
        assert sanitize_folder_name("") == "unnamed_conversation"
    
    def test_none(self):
        assert sanitize_folder_name(None) == "unnamed_conversation"
    
    def test_long_folder_name(self):
        long_name = "a" * 300
        result = sanitize_folder_name(long_name)
        assert len(result) == 255
        assert result == "a" * 255
    
    def test_leading_trailing_dots(self):
        assert sanitize_folder_name("...folder...") == "folder"


class TestValidateEmail:
    """Tests for validate_email function."""
    
    def test_valid_emails(self):
        valid_emails = [
            "user@example.com",
            "user.name@example.com",
            "user+tag@example.co.uk",
            "user_name@example-domain.com",
            "user123@example123.com",
        ]
        for email in valid_emails:
            assert validate_email(email) is True
    
    def test_invalid_emails(self):
        invalid_emails = [
            "",
            "notanemail",
            "@example.com",
            "user@",
            "user@.com",
            "user@@example.com",
            "user@example",
            "user@exam ple.com",
        ]
        for email in invalid_emails:
            assert validate_email(email) is False
    
    def test_none(self):
        assert validate_email(None) is False
    
    def test_empty_string(self):
        assert validate_email("") is False
    
    def test_with_whitespace(self):
        assert validate_email(" user@example.com ") is True
        assert validate_email("user@example.com ") is True


class TestValidateChannelId:
    """Tests for validate_channel_id function."""
    
    def test_valid_channel_ids(self):
        valid_ids = [
            "C01234567",
            "C012345678",
            "C0123456789",
            "D01234567",
            "D012345678",
            "G01234567",
            "G012345678",
        ]
        for channel_id in valid_ids:
            assert validate_channel_id(channel_id) is True
    
    def test_invalid_channel_ids(self):
        invalid_ids = [
            "",
            None,
            "invalid",
            "C0123456",  # Too short
            "C01234567890",  # Too long
            "X01234567",  # Wrong prefix
            "c01234567",  # Lowercase prefix
            "C0123456-",  # Invalid character
        ]
        for channel_id in invalid_ids:
            assert validate_channel_id(channel_id) is False


class TestValidateChannelsJson:
    """Tests for validate_channels_json function."""
    
    def test_valid_structure(self):
        data = {
            "channels": [
                {"id": "C01234567", "export": True},
                {"id": "D01234567", "export": False}
            ]
        }
        assert validate_channels_json(data) is True
    
    def test_missing_channels_key(self):
        data = {"other": "value"}
        with pytest.raises(ValueError, match="channels.json must contain 'channels' key"):
            validate_channels_json(data)
    
    def test_not_dict(self):
        data = ["not", "a", "dict"]
        with pytest.raises(ValueError, match="channels.json must be a JSON object"):
            validate_channels_json(data)
    
    def test_channels_not_list(self):
        data = {"channels": "not a list"}
        with pytest.raises(ValueError, match="'channels' must be a list"):
            validate_channels_json(data)


class TestValidatePeopleJson:
    """Tests for validate_people_json function."""
    
    def test_valid_structure(self):
        data = {
            "people": [
                {"slackId": "U01234567", "email": "user@example.com", "displayName": "User"},
            ]
        }
        assert validate_people_json(data) is True
    
    def test_missing_people_key(self):
        data = {"other": "value"}
        with pytest.raises(ValueError, match="people.json must contain 'people' key"):
            validate_people_json(data)
    
    def test_not_dict(self):
        data = ["not", "a", "dict"]
        with pytest.raises(ValueError, match="people.json must be a JSON object"):
            validate_people_json(data)
    
    def test_people_not_list(self):
        data = {"people": "not a list"}
        with pytest.raises(ValueError, match="'people' must be a list"):
            validate_people_json(data)
    
    def test_person_not_dict(self):
        data = {"people": ["not", "a", "dict"]}
        with pytest.raises(ValueError, match="Each person must be a dictionary"):
            validate_people_json(data)
    
    def test_missing_slack_id(self):
        data = {"people": [{"email": "user@example.com"}]}
        with pytest.raises(ValueError, match="Each person must have 'slackId'"):
            validate_people_json(data)


class TestFormatTimestamp:
    """Tests for format_timestamp function."""
    
    def test_valid_timestamp(self):
        # Unix timestamp for 2024-01-01 00:00:00 UTC
        ts = "1704067200.0"
        result = format_timestamp(ts)
        assert isinstance(result, str)
        assert "2024-01-01" in result
        assert "UTC" in result
    
    def test_invalid_timestamp(self):
        assert format_timestamp("invalid") == "invalid"
        assert format_timestamp("") == ""
    
    def test_none(self):
        # Should handle gracefully - returns the input
        result = format_timestamp(None)
        assert result is None


class TestConvertDateToTimestamp:
    """Tests for convert_date_to_timestamp function."""
    
    def test_date_only_format(self):
        result = convert_date_to_timestamp("2024-01-01")
        assert result is not None
        assert isinstance(result, str)
        # Verify it's a valid timestamp
        dt = datetime.fromtimestamp(float(result), tz=timezone.utc)
        assert dt.year == 2024
        assert dt.month == 1
        assert dt.day == 1
    
    def test_datetime_format(self):
        result = convert_date_to_timestamp("2024-01-01 12:30:45")
        assert result is not None
        dt = datetime.fromtimestamp(float(result), tz=timezone.utc)
        assert dt.hour == 12
        assert dt.minute == 30
        assert dt.second == 45
    
    def test_end_date_sets_end_of_day(self):
        result = convert_date_to_timestamp("2024-01-01", is_end_date=True)
        assert result is not None
        dt = datetime.fromtimestamp(float(result), tz=timezone.utc)
        assert dt.hour == 23
        assert dt.minute == 59
        assert dt.second == 59
    
    def test_invalid_format(self):
        assert convert_date_to_timestamp("invalid") is None
        assert convert_date_to_timestamp("2024-13-01") is None  # Invalid month
    
    def test_none(self):
        assert convert_date_to_timestamp(None) is None
    
    def test_empty_string(self):
        assert convert_date_to_timestamp("") is None
        assert convert_date_to_timestamp("   ") is None


class TestLoadJsonFile:
    """Tests for load_json_file function."""
    
    def test_load_valid_json(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump({"key": "value"}, f)
            temp_path = f.name
        
        try:
            result = load_json_file(temp_path)
            assert result == {"key": "value"}
        finally:
            os.unlink(temp_path)
    
    def test_load_nonexistent_file(self):
        result = load_json_file("/nonexistent/file.json")
        assert result is None
    
    def test_load_invalid_json(self):
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write("{ invalid json }")
            temp_path = f.name
        
        try:
            result = load_json_file(temp_path)
            assert result is None
        finally:
            os.unlink(temp_path)


class TestSaveJsonFile:
    """Tests for save_json_file function."""
    
    def test_save_valid_data(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            filepath = os.path.join(temp_dir, "test.json")
            data = {"key": "value", "list": [1, 2, 3]}
            
            result = save_json_file(data, filepath)
            assert result is True
            assert os.path.exists(filepath)
            
            # Verify content
            with open(filepath, 'r') as f:
                loaded = json.load(f)
            assert loaded == data
    
    def test_save_creates_directory(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            filepath = os.path.join(temp_dir, "subdir", "test.json")
            data = {"key": "value"}
            
            result = save_json_file(data, filepath)
            assert result is True
            assert os.path.exists(filepath)
    
    def test_save_with_nested_data(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            filepath = os.path.join(temp_dir, "test.json")
            data = {
                "channels": [
                    {"id": "C123", "name": "test"},
                    {"id": "D456", "name": "dm"}
                ]
            }
            
            result = save_json_file(data, filepath)
            assert result is True
            
            # Verify can be loaded back
            loaded = load_json_file(filepath)
            assert loaded == data
