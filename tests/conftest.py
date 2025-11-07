"""
Pytest configuration and shared fixtures.
"""

import os
import shutil
import tempfile

import pytest


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    temp_path = tempfile.mkdtemp()
    yield temp_path
    shutil.rmtree(temp_path)


@pytest.fixture
def temp_file():
    """Create a temporary file."""
    with tempfile.NamedTemporaryFile(mode="w", delete=False) as f:
        temp_path = f.name
    yield temp_path
    if os.path.exists(temp_path):
        os.unlink(temp_path)


@pytest.fixture
def sample_channels_json():
    """Sample channels.json data for testing."""
    return {
        "channels": [
            {"id": "C01234567", "displayName": "general", "export": True},
            {"id": "C01234568", "displayName": "random", "export": False},
            {"id": "D01234567", "export": True},
        ]
    }


@pytest.fixture
def sample_people_json():
    """Sample people.json data for testing."""
    return {
        "people": [
            {"slackId": "U01234567", "email": "user1@example.com", "displayName": "User One"},
            {"slackId": "U01234568", "email": "user2@example.com", "displayName": "User Two"},
        ]
    }
