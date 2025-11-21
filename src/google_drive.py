import logging
import os
import platform
import re
import shutil
import time
from datetime import datetime, timezone
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload, MediaIoBaseUpload

logger = logging.getLogger(__name__)

# Constants
SECURE_FILE_PERMISSIONS = 0o600
GOOGLE_DRIVE_MAX_FOLDER_NAME_LENGTH = 255  # Maximum folder name length in Google Drive
GOOGLE_DRIVE_FOLDER_ID_MIN_LENGTH = 10
GOOGLE_DRIVE_FOLDER_ID_MAX_LENGTH = 50
API_TIMEOUT_SECONDS = 30
# Rate limiting for Google Drive API (requests per 100 seconds)
GOOGLE_DRIVE_RATE_LIMIT_DELAY = 0.5  # seconds between API calls
GOOGLE_DRIVE_BATCH_SIZE = 10  # number of calls before adding extra delay
GOOGLE_DRIVE_BATCH_DELAY = 1.0  # extra delay after batch
# Google Drive API OAuth scopes
GOOGLE_DRIVE_SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
]


class GoogleDriveClient:
    def __init__(self, credentials_file: str):
        """Initialize Google Drive client with authentication.

        Args:
            credentials_file: Path to Google Drive API credentials JSON file

        Raises:
            Exception: If authentication fails or service cannot be built
        """
        try:
            self.creds = self._authenticate(credentials_file)
            if not self.creds:
                raise ValueError("Failed to obtain valid credentials")
            self.service = build("drive", "v3", credentials=self.creds)
            if not self.service:
                raise ValueError("Failed to build Google Drive service")
            self.docs_service = build("docs", "v1", credentials=self.creds)
            if not self.docs_service:
                raise ValueError("Failed to build Google Docs service")
            # Rate limiting state
            self._last_api_call_time = 0.0
            self._api_call_count = 0
        except Exception as e:
            logger.error(f"Failed to initialize Google Drive client: {e}")
            raise

    def _escape_drive_query_string(self, value: str) -> str:
        """Properly escape strings for Google Drive API queries.

        Args:
            value: String to escape

        Returns:
            Escaped string safe for use in Drive API queries
        """
        if not value:
            return ""
        # Escape backslashes first (must be first)
        escaped = value.replace("\\", "\\\\")
        # Escape single quotes
        escaped = escaped.replace("'", "\\'")
        # Escape double quotes if using alternative query format
        escaped = escaped.replace('"', '\\"')
        return escaped

    def _validate_folder_id(self, folder_id: Optional[str]) -> bool:
        """Validate Google Drive folder ID format.

        Args:
            folder_id: Folder ID to validate

        Returns:
            True if valid format, False otherwise
        """
        if not folder_id:
            return False
        if not isinstance(folder_id, str):
            return False
        if (
            len(folder_id) < GOOGLE_DRIVE_FOLDER_ID_MIN_LENGTH
            or len(folder_id) > GOOGLE_DRIVE_FOLDER_ID_MAX_LENGTH
        ):
            return False
        # Basic format check - Google Drive IDs are alphanumeric with possible underscores/hyphens
        if not re.match(r"^[a-zA-Z0-9_-]+$", folder_id):
            return False
        return True

    def _lock_file(self, file_handle):
        """Lock file for exclusive access (platform-specific)."""
        if platform.system() == "Windows":
            try:
                import msvcrt

                msvcrt.locking(file_handle.fileno(), msvcrt.LK_LOCK, 1)
            except ImportError:
                # msvcrt not available, skip locking on Windows
                logger.debug("File locking not available on this Windows system")
        else:
            try:
                import fcntl

                fcntl.flock(file_handle.fileno(), fcntl.LOCK_EX)
            except ImportError:
                logger.debug("fcntl not available, skipping file lock")

    def _unlock_file(self, file_handle):
        """Unlock file (platform-specific)."""
        if platform.system() == "Windows":
            try:
                import msvcrt

                msvcrt.locking(file_handle.fileno(), msvcrt.LK_UNLCK, 1)
            except ImportError:
                pass
        else:
            try:
                import fcntl

                fcntl.flock(file_handle.fileno(), fcntl.LOCK_UN)
            except ImportError:
                pass

    @staticmethod
    def _write_token_file_core(
        token_path: str,
        creds: Credentials,
        use_locking: bool = False,
        lock_file_func=None,
        unlock_file_func=None,
    ) -> None:
        """Core function to safely write token file with optional locking.

        Args:
            token_path: Path to token file
            creds: Credentials object to save
            use_locking: Whether to use file locking (requires lock_file_func and unlock_file_func)
            lock_file_func: Optional function to lock the file
            unlock_file_func: Optional function to unlock the file
        """
        temp_path = token_path + ".tmp"
        try:
            # Ensure directory exists
            token_dir = os.path.dirname(token_path) if os.path.dirname(token_path) else "."
            try:
                os.makedirs(token_dir, exist_ok=True)
            except OSError as e:
                logger.error(f"Failed to create token directory {token_dir}: {e}")
                raise

            with open(temp_path, "w") as f:
                if use_locking and lock_file_func and unlock_file_func:
                    # Lock file for exclusive write
                    try:
                        lock_file_func(f)
                        f.write(creds.to_json())
                        f.flush()
                        os.fsync(f.fileno())  # Ensure data is written to disk
                    finally:
                        unlock_file_func(f)
                else:
                    # Write without locking
                    f.write(creds.to_json())

            # Set secure file permissions on temp file before move (fixes race condition)
            try:
                os.chmod(temp_path, SECURE_FILE_PERMISSIONS)
                logger.debug(f"Set secure permissions for token file: {temp_path}")
            except OSError as e:
                logger.warning(f"Could not set permissions on temp token file {temp_path}: {e}")

            # Atomic move
            shutil.move(temp_path, token_path)

            # Verify permissions after move (should already be set, but double-check)
            try:
                current_mode = os.stat(token_path).st_mode & 0o777
                if current_mode != SECURE_FILE_PERMISSIONS:
                    os.chmod(token_path, SECURE_FILE_PERMISSIONS)
                    logger.debug(f"Corrected permissions for token file: {token_path}")
            except OSError as e:
                logger.warning(f"Could not verify permissions on token file {token_path}: {e}")

            logger.debug(f"Token saved successfully to {token_path}")
        except Exception as e:
            logger.error(f"Failed to save token: {e}")
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
            raise

    def _save_token_safely(self, token_path, creds):
        """Safely write token file with locking to prevent corruption.

        Args:
            token_path: Path to token file
            creds: Credentials object to save
        """
        self._write_token_file_core(
            token_path,
            creds,
            use_locking=True,
            lock_file_func=self._lock_file,
            unlock_file_func=self._unlock_file,
        )

    def _rate_limit(self):
        """Apply rate limiting for Google Drive API calls."""
        current_time = time.time()
        time_since_last_call = current_time - self._last_api_call_time

        # Always add base delay between calls
        if time_since_last_call < GOOGLE_DRIVE_RATE_LIMIT_DELAY:
            sleep_time = GOOGLE_DRIVE_RATE_LIMIT_DELAY - time_since_last_call
            time.sleep(sleep_time)

        # After batch_size calls, add extra delay
        self._api_call_count += 1
        if self._api_call_count >= GOOGLE_DRIVE_BATCH_SIZE:
            time.sleep(GOOGLE_DRIVE_BATCH_DELAY)
            self._api_call_count = 0

        self._last_api_call_time = time.time()

    @staticmethod
    def setup_authentication(credentials_file: str) -> str:
        """Set up Google Drive authentication and create token file for CI/CD.

        This method performs the OAuth flow to authorize access and saves the token file.
        It's designed to be run once locally to create a token file that can be used in CI/CD.

        Args:
            credentials_file: Path to Google Drive API credentials JSON file

        Returns:
            Path to the created token file

        Raises:
            Exception: If authentication fails
        """
        creds = None

        # Securely determine token path
        default_token_dir = os.path.join(os.path.expanduser("~"), ".config", "slackfeeder")
        token_path = os.getenv(
            "GOOGLE_DRIVE_TOKEN_FILE", os.path.join(default_token_dir, "token.json")
        )

        scopes = GOOGLE_DRIVE_SCOPES

        if os.path.exists(token_path):
            # Check for insecure file permissions and enforce security
            try:
                file_mode = os.stat(token_path).st_mode
                if file_mode & 0o077:  # Check if group or others have permissions
                    logger.error(
                        f"Token file {token_path} has insecure permissions. Aborting authentication."
                    )
                    raise PermissionError(
                        f"Token file permissions are insecure (current mode: {oct(file_mode & 0o777)}). Required: {oct(SECURE_FILE_PERMISSIONS)}"
                    )
            except OSError as e:
                logger.error(f"Could not check permissions for token file {token_path}: {e}")
                raise

            try:
                creds = Credentials.from_authorized_user_file(token_path, scopes)
            except Exception as e:
                logger.warning(f"Error loading token file {token_path}: {e}")

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except Exception as e:
                    logger.warning(f"Error refreshing token: {e}")
                    creds = None

            if not creds or not creds.valid:
                logger.info("Starting OAuth flow. A browser window will open for authorization...")
                flow = InstalledAppFlow.from_client_secrets_file(credentials_file, scopes)
                creds = flow.run_local_server(port=0)
                logger.info("Authorization successful!")

            # Save token using the same safe method
            GoogleDriveClient._save_token_safely_static(token_path, creds)
            logger.info(f"Token file created successfully at: {token_path}")
            logger.info(
                f"Token file permissions: {oct(SECURE_FILE_PERMISSIONS)} (owner read/write only)"
            )

        return token_path

    @staticmethod
    def _save_token_safely_static(token_path: str, creds: Credentials) -> None:
        """Static version of _save_token_safely for use without instance."""
        GoogleDriveClient._write_token_file_core(token_path, creds, use_locking=False)

    def _authenticate(self, credentials_file):
        """Authenticates with Google Drive API."""
        creds = None

        # Securely determine token path
        default_token_dir = os.path.join(os.path.expanduser("~"), ".config", "slackfeeder")
        token_path = os.getenv(
            "GOOGLE_DRIVE_TOKEN_FILE", os.path.join(default_token_dir, "token.json")
        )

        scopes = GOOGLE_DRIVE_SCOPES

        if os.path.exists(token_path):
            # Check for insecure file permissions and enforce security
            try:
                file_mode = os.stat(token_path).st_mode
                if file_mode & 0o077:  # Check if group or others have permissions
                    logger.error(
                        f"Token file {token_path} has insecure permissions. Aborting authentication."
                    )
                    raise PermissionError(
                        f"Token file permissions are insecure (current mode: {oct(file_mode & 0o777)}). Required: {oct(SECURE_FILE_PERMISSIONS)}"
                    )
            except OSError as e:
                logger.error(f"Could not check permissions for token file {token_path}: {e}")
                raise

            try:
                creds = Credentials.from_authorized_user_file(token_path, scopes)
            except Exception as e:
                logger.warning(f"Error loading token file {token_path}: {e}")

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except Exception as e:
                    logger.warning(f"Error refreshing token: {e}")
                    creds = None

            if not creds or not creds.valid:
                flow = InstalledAppFlow.from_client_secrets_file(credentials_file, scopes)
                creds = flow.run_local_server(port=0)

            self._save_token_safely(token_path, creds)

        return creds

    def find_folder(
        self, folder_name: str, parent_folder_id: Optional[str] = None
    ) -> Optional[str]:
        """Finds an existing folder by name in Google Drive.

        Args:
            folder_name: Name of the folder to find
            parent_folder_id: Optional parent folder ID to search within

        Returns:
            Folder ID if found, None otherwise
        """
        # Validate parent folder ID if provided
        if parent_folder_id and not self._validate_folder_id(parent_folder_id):
            logger.warning(f"Invalid parent folder ID format: {parent_folder_id}")
            return None

        escaped_name = self._escape_drive_query_string(folder_name)
        query = f"name='{escaped_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        if parent_folder_id:
            # Escape the folder ID to prevent injection
            escaped_parent_id = self._escape_drive_query_string(parent_folder_id)
            query += f" and '{escaped_parent_id}' in parents"

        try:
            self._rate_limit()
            results = (
                self.service.files().list(q=query, fields="files(id, name)", pageSize=1).execute()
            )
            items = results.get("files", [])
            if items:
                return items[0]["id"]
        except HttpError as error:
            logger.warning(f"Error searching for folder '{folder_name}': {error}")
        return None

    def create_folder(
        self, folder_name: str, parent_folder_id: Optional[str] = None
    ) -> Optional[str]:
        """Creates a folder in Google Drive, or returns existing folder if found.

        Args:
            folder_name: Name of the folder to create
            parent_folder_id: Optional parent folder ID

        Returns:
            Folder ID if successful, None otherwise
        """
        if not folder_name:
            logger.error("Folder name cannot be empty")
            return None

        # Validate parent folder ID if provided
        if parent_folder_id and not self._validate_folder_id(parent_folder_id):
            logger.error(f"Invalid parent folder ID format: {parent_folder_id}")
            return None

        # Validate folder name length (Google Drive limit is 255 characters)
        if len(folder_name) > GOOGLE_DRIVE_MAX_FOLDER_NAME_LENGTH:
            logger.warning(
                f"Folder name exceeds {GOOGLE_DRIVE_MAX_FOLDER_NAME_LENGTH} characters, truncating: {folder_name[:GOOGLE_DRIVE_MAX_FOLDER_NAME_LENGTH]}"
            )
            folder_name = folder_name[:GOOGLE_DRIVE_MAX_FOLDER_NAME_LENGTH].rstrip(". ")

        # First check if folder already exists
        existing_folder_id = self.find_folder(folder_name, parent_folder_id)
        if existing_folder_id:
            logger.info(f"Found existing folder '{folder_name}' with ID: {existing_folder_id}")
            return existing_folder_id

        file_metadata = {"name": folder_name, "mimeType": "application/vnd.google-apps.folder"}
        if parent_folder_id:
            file_metadata["parents"] = [parent_folder_id]

        try:
            self._rate_limit()
            folder = self.service.files().create(body=file_metadata, fields="id").execute()
            logger.info(f"Created folder '{folder_name}' with ID: {folder.get('id')}")
            return folder.get("id")
        except HttpError as error:
            logger.error(f"An error occurred while creating folder '{folder_name}': {error}")
            return None

    def upload_file(self, file_path: str, folder_id: str, overwrite: bool = False) -> Optional[str]:
        """Uploads a file to a specific folder in Google Drive.

        Args:
            file_path: Local path to file to upload
            folder_id: Google Drive folder ID where to upload
            overwrite: If True, delete existing file with same name first

        Returns:
            File ID if successful, None otherwise
        """
        if not os.path.exists(file_path):
            logger.error(f"File not found: {file_path}")
            return None

        # Validate folder ID
        if not self._validate_folder_id(folder_id):
            logger.error(f"Invalid folder ID format: {folder_id}")
            return None

        file_name = os.path.basename(file_path)

        # Check if file already exists
        if overwrite:
            self._rate_limit()  # Rate limit before API call
            escaped_file_name = self._escape_drive_query_string(file_name)
            # Escape folder_id in query
            escaped_folder_id = self._escape_drive_query_string(folder_id)
            query = (
                f"name='{escaped_file_name}' and '{escaped_folder_id}' in parents and trashed=false"
            )
            try:
                results = (
                    self.service.files()
                    .list(q=query, fields="files(id, name)", pageSize=1)
                    .execute()
                )
                existing_files = results.get("files", [])
                if existing_files:
                    # Delete existing file
                    try:
                        existing_file_id = existing_files[0]["id"]
                        self._rate_limit()
                        self.service.files().delete(fileId=existing_file_id).execute()
                        logger.info(
                            f"Deleted existing file '{file_name}' before uploading new version"
                        )
                    except HttpError as error:
                        if error.resp.status == 404:
                            # File doesn't exist, that's fine (might have been deleted concurrently)
                            logger.debug(
                                f"File '{file_name}' not found for deletion (already gone)"
                            )
                        else:
                            logger.error(f"Failed to delete existing file '{file_name}': {error}")
                            if overwrite:
                                # If overwrite is requested and deletion fails, return None
                                return None
            except HttpError as error:
                logger.warning(f"Error checking for existing file '{file_name}': {error}")

        media = MediaFileUpload(file_path, mimetype="text/plain")
        file_metadata = {"name": file_name, "parents": [folder_id]}

        try:
            file = (
                self.service.files()
                .create(body=file_metadata, media_body=media, fields="id")
                .execute()
            )
            logger.info(f"Uploaded file '{file_name}' with ID: {file.get('id')}")
            return file.get("id")
        except HttpError as error:
            logger.error(f"An error occurred while uploading file '{file_name}': {error}")
            return None

    def _extract_message_timestamps_from_doc(self, doc_id: str) -> set:
        """Extract message timestamps from an existing Google Doc.

        Reads the document content and extracts Unix timestamps from formatted
        message timestamps (e.g., "[2025-11-21 10:30:45 UTC]").

        Args:
            doc_id: Google Drive document ID

        Returns:
            Set of Unix timestamp strings found in the document
        """
        timestamps = set()
        try:
            self._rate_limit()
            doc = self.docs_service.documents().get(documentId=doc_id).execute()
            
            # Extract text content from the document
            content_parts = []
            if "body" in doc and "content" in doc["body"]:
                for element in doc["body"]["content"]:
                    if "paragraph" in element:
                        for para_element in element["paragraph"].get("elements", []):
                            if "textRun" in para_element:
                                content_parts.append(para_element["textRun"].get("content", ""))
            
            content = "".join(content_parts)
            
            # Pattern to match formatted timestamps: [YYYY-MM-DD HH:MM:SS UTC]
            # This matches the format used by format_timestamp()
            timestamp_pattern = re.compile(r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) UTC\]')
            
            for match in timestamp_pattern.finditer(content):
                timestamp_str = match.group(1)
                try:
                    # Convert formatted timestamp back to Unix timestamp
                    dt = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
                    dt = dt.replace(tzinfo=timezone.utc)
                    unix_ts = str(dt.timestamp())
                    timestamps.add(unix_ts)
                except (ValueError, TypeError):
                    # Skip invalid timestamps
                    continue
                    
        except HttpError as error:
            logger.warning(f"Error reading document content for timestamp extraction: {error}")
        except Exception as e:
            logger.debug(f"Error extracting timestamps from document: {e}")
            
        return timestamps

    def create_or_update_google_doc(
        self, doc_name: str, content: str, folder_id: str, overwrite: bool = False
    ) -> Optional[str]:
        """Creates a new Google Doc or appends to an existing one.

        Args:
            doc_name: Name of the Google Doc (without .txt extension)
            content: Text content to add to the document
            folder_id: Google Drive folder ID where to create the doc
            overwrite: If True and doc exists, replace content instead of appending

        Returns:
            Document ID if successful, None otherwise
        """
        # Validate folder ID
        if not self._validate_folder_id(folder_id):
            logger.error(f"Invalid folder ID format: {folder_id}")
            return None

        if not content:
            logger.warning(f"Empty content provided for doc '{doc_name}', skipping")
            return None

        # Check if document already exists - get ALL matches, not just first
        escaped_doc_name = self._escape_drive_query_string(doc_name)
        escaped_folder_id = self._escape_drive_query_string(folder_id)
        query = (
            f"name='{escaped_doc_name}' and '{escaped_folder_id}' in parents "
            f"and mimeType='application/vnd.google-apps.document' and trashed=false"
        )

        existing_doc_id = None
        existing_files = []
        try:
            self._rate_limit()
            results = (
                self.service.files()
                .list(q=query, fields="files(id, name, modifiedTime)", pageSize=100)
                .execute()
            )
            existing_files = results.get("files", [])
            
            if len(existing_files) > 1:
                # Multiple documents with same name - use most recently modified
                logger.warning(
                    f"Found {len(existing_files)} documents with name '{doc_name}'. "
                    f"Using most recently modified document."
                )
                # Sort by modifiedTime descending, use the most recent
                existing_files.sort(
                    key=lambda x: x.get("modifiedTime", ""), reverse=True
                )
                existing_doc_id = existing_files[0]["id"]
                # Log warning about duplicates
                duplicate_ids = [f["id"] for f in existing_files[1:]]
                logger.warning(
                    f"Duplicate documents found (IDs: {', '.join(duplicate_ids)}). "
                    f"Consider cleaning up duplicates manually."
                )
            elif existing_files:
                existing_doc_id = existing_files[0]["id"]
        except HttpError as error:
            logger.warning(f"Error checking for existing doc '{doc_name}': {error}")

        if existing_doc_id:
            if overwrite:
                # Replace entire content
                try:
                    # Get current document to find end index
                    self._rate_limit()
                    doc = self.docs_service.documents().get(documentId=existing_doc_id).execute()
                    end_index = doc.get("body", {}).get("content", [{}])[-1].get("endIndex", 1)

                    # Delete all content except the last newline
                    if end_index > 1:
                        requests = [
                            {
                                "deleteContentRange": {
                                    "range": {
                                        "startIndex": 1,
                                        "endIndex": end_index - 1,
                                    }
                                }
                            }
                        ]
                        self._rate_limit()
                        self.docs_service.documents().batchUpdate(
                            documentId=existing_doc_id, body={"requests": requests}
                        ).execute()

                    # Insert new content
                    requests = [
                        {
                            "insertText": {
                                "location": {"index": 1},
                                "text": content,
                            }
                        }
                    ]
                    self._rate_limit()
                    self.docs_service.documents().batchUpdate(
                        documentId=existing_doc_id, body={"requests": requests}
                    ).execute()
                    logger.info(f"Replaced content in existing doc '{doc_name}'")
                    return existing_doc_id
                except HttpError as error:
                    logger.error(f"Error replacing content in doc '{doc_name}': {error}")
                    return None
            else:
                # Append content - but first check for duplicates
                try:
                    # Extract timestamps from existing document
                    existing_timestamps = self._extract_message_timestamps_from_doc(existing_doc_id)
                    
                    # Extract timestamps from content being appended
                    # Pattern to match formatted timestamps: [YYYY-MM-DD HH:MM:SS UTC]
                    timestamp_pattern = re.compile(r'\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) UTC\]')
                    content_timestamps = set()
                    for match in timestamp_pattern.finditer(content):
                        timestamp_str = match.group(1)
                        try:
                            dt = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
                            dt = dt.replace(tzinfo=timezone.utc)
                            unix_ts = str(dt.timestamp())
                            content_timestamps.add(unix_ts)
                        except (ValueError, TypeError):
                            continue
                    
                    # Filter out content lines that have timestamps already in the document
                    if existing_timestamps and content_timestamps:
                        # Find which timestamps are duplicates
                        duplicate_timestamps = content_timestamps.intersection(existing_timestamps)
                        
                        if duplicate_timestamps:
                            logger.info(
                                f"Found {len(duplicate_timestamps)} duplicate message(s) in existing doc. "
                                f"Filtering out duplicates before appending."
                            )
                            
                            # Filter content by removing lines with duplicate timestamps
                            filtered_lines = []
                            current_message_lines = []
                            current_has_duplicate = False
                            
                            for line in content.split('\n'):
                                # Check if this line contains a timestamp
                                match = timestamp_pattern.search(line)
                                if match:
                                    # Process previous message if any
                                    if current_message_lines and not current_has_duplicate:
                                        filtered_lines.extend(current_message_lines)
                                    
                                    # Start new message
                                    timestamp_str = match.group(1)
                                    try:
                                        dt = datetime.strptime(timestamp_str, "%Y-%m-%d %H:%M:%S")
                                        dt = dt.replace(tzinfo=timezone.utc)
                                        unix_ts = str(dt.timestamp())
                                        current_has_duplicate = unix_ts in existing_timestamps
                                    except (ValueError, TypeError):
                                        current_has_duplicate = False
                                    
                                    current_message_lines = [line]
                                else:
                                    # Continuation of current message
                                    if current_message_lines:
                                        current_message_lines.append(line)
                                    else:
                                        # Line without timestamp (separator, etc.) - keep it
                                        filtered_lines.append(line)
                            
                            # Process last message
                            if current_message_lines and not current_has_duplicate:
                                filtered_lines.extend(current_message_lines)
                            
                            content = '\n'.join(filtered_lines)
                            
                            if not content.strip():
                                logger.info(
                                    f"All messages in content are duplicates. "
                                    f"Skipping append to '{doc_name}'"
                                )
                                return existing_doc_id
                    
                    # Get current document to find end index
                    self._rate_limit()
                    doc = self.docs_service.documents().get(documentId=existing_doc_id).execute()
                    end_index = doc.get("body", {}).get("content", [{}])[-1].get("endIndex", 1)

                    # Insert new content at the end (before the last newline)
                    insert_index = max(1, end_index - 1)
                    requests = [
                        {
                            "insertText": {
                                "location": {"index": insert_index},
                                "text": content,
                            }
                        }
                    ]
                    self._rate_limit()
                    self.docs_service.documents().batchUpdate(
                        documentId=existing_doc_id, body={"requests": requests}
                    ).execute()
                    logger.info(f"Appended content to existing doc '{doc_name}'")
                    return existing_doc_id
                except HttpError as error:
                    logger.error(f"Error appending content to doc '{doc_name}': {error}")
                    return None
        else:
            # Create new document
            try:
                # Create the document
                self._rate_limit()
                doc = self.docs_service.documents().create(body={"title": doc_name}).execute()
                doc_id = doc.get("documentId")
                if not doc_id:
                    logger.error(f"Failed to get document ID for '{doc_name}'")
                    return None

                # Move document to the specified folder
                self._rate_limit()
                file = self.service.files().get(fileId=doc_id, fields="parents").execute()
                previous_parents = ",".join(file.get("parents", []))
                self._rate_limit()
                self.service.files().update(
                    fileId=doc_id,
                    addParents=folder_id,
                    removeParents=previous_parents,
                    fields="id, parents",
                ).execute()

                # Insert content
                requests = [
                    {
                        "insertText": {
                            "location": {"index": 1},
                            "text": content,
                        }
                    }
                ]
                self._rate_limit()
                self.docs_service.documents().batchUpdate(
                    documentId=doc_id, body={"requests": requests}
                ).execute()

                logger.info(f"Created new Google Doc '{doc_name}' with ID: {doc_id}")
                return doc_id
            except HttpError as error:
                logger.error(f"Error creating Google Doc '{doc_name}': {error}")
                return None

    def list_files_in_folder(self, folder_id: str, name_pattern: Optional[str] = None) -> list:
        """Lists files in a Google Drive folder.

        Args:
            folder_id: Google Drive folder ID
            name_pattern: Optional pattern to filter files by name (e.g., "_history_")

        Returns:
            List of file metadata dictionaries with 'id', 'name', 'createdTime', 'modifiedTime'
        """
        # Validate folder ID
        if not self._validate_folder_id(folder_id):
            logger.error(f"Invalid folder ID format: {folder_id}")
            return []

        query = f"'{folder_id}' in parents and trashed=false and mimeType!='application/vnd.google-apps.folder'"
        if name_pattern:
            escaped_pattern = self._escape_drive_query_string(name_pattern)
            query += f" and name contains '{escaped_pattern}'"

        files = []
        try:
            self._rate_limit()
            results = (
                self.service.files()
                .list(
                    q=query,
                    fields="files(id, name, createdTime, modifiedTime)",
                    orderBy="modifiedTime desc",
                    pageSize=100,
                )
                .execute()
            )
            files = results.get("files", [])
        except HttpError as error:
            logger.warning(f"Error listing files in folder {folder_id}: {error}")
        return files

    def get_latest_export_timestamp(self, folder_id: str, file_prefix: str) -> Optional[str]:
        """Gets the timestamp from the most recent export metadata file.

        First tries to read from the metadata JSON file ({prefix}_last_export.json),
        which contains the actual latest message timestamp. Falls back to parsing
        filenames if metadata file doesn't exist (for backward compatibility).

        Args:
            folder_id: Google Drive folder ID
            file_prefix: Prefix to match export files (sanitized channel name)

        Returns:
            Unix timestamp string from the most recent export, or None if no files found
        """
        import io
        import json
        import re
        from datetime import datetime, timezone

        from googleapiclient.http import MediaIoBaseDownload

        # First, try to read from the metadata JSON file
        metadata_filename = f"{file_prefix}_last_export.json"
        escaped_metadata_name = self._escape_drive_query_string(metadata_filename)
        query = f"name='{escaped_metadata_name}' and '{folder_id}' in parents and trashed=false"

        try:
            self._rate_limit()
            results = (
                self.service.files().list(q=query, fields="files(id, name)", pageSize=1).execute()
            )
            metadata_files = results.get("files", [])

            if metadata_files:
                # Found metadata file, read it
                metadata_file_id = metadata_files[0]["id"]
                request = self.service.files().get_media(fileId=metadata_file_id)
                file_content = io.BytesIO()
                downloader = MediaIoBaseDownload(file_content, request)

                done = False
                while not done:
                    status, done = downloader.next_chunk()

                file_content.seek(0)
                metadata_json = json.loads(file_content.read().decode("utf-8"))
                latest_message_timestamp = metadata_json.get("latest_message_timestamp")

                if latest_message_timestamp:
                    logger.debug(
                        f"Found latest export timestamp from metadata file: {latest_message_timestamp}"
                    )
                    return str(latest_message_timestamp)
        except HttpError as error:
            logger.debug(f"Could not read metadata file (may not exist yet): {error}")
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.debug(f"Could not parse metadata file: {e}")
        except Exception as e:
            logger.debug(f"Error reading metadata file: {e}")

        # Fallback: parse timestamps from filenames (for backward compatibility)
        files = self.list_files_in_folder(folder_id, name_pattern=f"{file_prefix}_history_")

        if not files:
            return None

        # Try to find the most recent file by parsing timestamps from filenames
        latest_timestamp = None
        latest_file_time = None

        # Pattern to match: {prefix}_history_YYYY-MM-DD_HH-MM-SS.txt
        pattern = re.compile(r"_history_(\d{4}-\d{2}-\d{2})_(\d{2})-(\d{2})-(\d{2})\.txt$")

        for file in files:
            filename = file.get("name", "")
            match = pattern.search(filename)
            if match:
                try:
                    # Parse the timestamp from filename
                    date_str = match.group(1)
                    hour = match.group(2)
                    minute = match.group(3)
                    second = match.group(4)
                    dt_str = f"{date_str} {hour}:{minute}:{second}"
                    dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
                    dt = dt.replace(tzinfo=timezone.utc)
                    file_timestamp = dt.timestamp()

                    # Keep track of the most recent
                    if latest_file_time is None or file_timestamp > latest_file_time:
                        latest_file_time = file_timestamp
                        # Use file modifiedTime as fallback
                        modified_time = file.get("modifiedTime")
                        if modified_time:
                            try:
                                from datetime import datetime as dt

                                mod_dt = dt.fromisoformat(modified_time.replace("Z", "+00:00"))
                                latest_timestamp = str(mod_dt.timestamp())
                            except Exception:
                                latest_timestamp = str(file_timestamp)
                        else:
                            latest_timestamp = str(file_timestamp)
                except Exception as e:
                    logger.debug(f"Could not parse timestamp from filename {filename}: {e}")
                    continue

        # If we didn't find a timestamp in filename, use the most recent file's modifiedTime
        if not latest_timestamp and files:
            try:
                most_recent_file = files[0]  # Already sorted by modifiedTime desc
                modified_time = most_recent_file.get("modifiedTime")
                if modified_time:
                    from datetime import datetime as dt

                    mod_dt = dt.fromisoformat(modified_time.replace("Z", "+00:00"))
                    latest_timestamp = str(mod_dt.timestamp())
            except Exception as e:
                logger.debug(f"Could not parse modifiedTime from file: {e}")

        if latest_timestamp:
            logger.debug(f"Using fallback timestamp from filename parsing: {latest_timestamp}")

        return latest_timestamp

    def save_export_metadata(
        self, folder_id: str, file_prefix: str, latest_message_timestamp: str
    ) -> bool:
        """Saves export metadata to a small JSON file in the folder.

        Args:
            folder_id: Google Drive folder ID
            file_prefix: Prefix for the metadata filename
            latest_message_timestamp: Unix timestamp string of the latest message

        Returns:
            True if successful, False otherwise
        """
        import io
        import json

        metadata_filename = f"{file_prefix}_last_export.json"
        metadata_content = {
            "latest_message_timestamp": float(latest_message_timestamp),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

        # Check if metadata file already exists
        escaped_metadata_name = self._escape_drive_query_string(metadata_filename)
        query = f"name='{escaped_metadata_name}' and '{folder_id}' in parents and trashed=false"

        try:
            self._rate_limit()
            results = (
                self.service.files().list(q=query, fields="files(id, name)", pageSize=1).execute()
            )
            existing_files = results.get("files", [])

            if existing_files:
                # Update existing file
                file_id = existing_files[0]["id"]
                metadata_json = json.dumps(metadata_content).encode("utf-8")
                media = MediaIoBaseUpload(
                    io.BytesIO(metadata_json), mimetype="application/json", resumable=False
                )

                self._rate_limit()
                self.service.files().update(fileId=file_id, media_body=media).execute()
                logger.debug(f"Updated metadata file {metadata_filename}")
            else:
                # Create new file
                metadata_json = json.dumps(metadata_content).encode("utf-8")
                media = MediaIoBaseUpload(
                    io.BytesIO(metadata_json), mimetype="application/json", resumable=False
                )
                file_metadata = {"name": metadata_filename, "parents": [folder_id]}

                self._rate_limit()
                self.service.files().create(
                    body=file_metadata, media_body=media, fields="id"
                ).execute()
                logger.debug(f"Created metadata file {metadata_filename}")

            return True
        except HttpError as error:
            logger.warning(f"Failed to save export metadata: {error}")
            return False
        except Exception as e:
            logger.warning(f"Error saving export metadata: {e}")
            return False

    def get_folder_permissions(self, folder_id: str) -> list:
        """Gets the list of permissions for a folder.

        Args:
            folder_id: Google Drive folder ID

        Returns:
            List of permission dictionaries with 'id', 'type', 'role', 'emailAddress'
        """
        # Validate folder ID
        if not self._validate_folder_id(folder_id):
            logger.error(f"Invalid folder ID format: {folder_id}")
            return []

        permissions = []
        try:
            self._rate_limit()
            results = (
                self.service.permissions()
                .list(fileId=folder_id, fields="permissions(id, type, role, emailAddress)")
                .execute()
            )
            permissions = results.get("permissions", [])
        except HttpError as error:
            logger.warning(f"Error listing permissions for folder {folder_id}: {error}")
        except Exception as e:
            logger.debug(f"Error getting folder permissions: {e}")

        return permissions

    def share_folder(
        self, folder_id: str, email_address: str, send_notification: bool = True
    ) -> bool:
        """Shares a folder with a specific user.

        Checks if the user already has access before attempting to share,
        preventing duplicate notifications.

        Args:
            folder_id: Google Drive folder ID to share
            email_address: Email address of user to share with
            send_notification: Whether to send email notification (default: True)

        Returns:
            True if successful or already shared, False otherwise
        """
        # Validate folder ID
        if not self._validate_folder_id(folder_id):
            logger.error(f"Invalid folder ID format: {folder_id}")
            return False

        if not email_address or not email_address.strip():
            logger.warning(f"Invalid email address provided: {email_address}")
            return False

        email_address = email_address.strip()

        # Check if user already has access
        existing_permissions = self.get_folder_permissions(folder_id)
        for perm in existing_permissions:
            if (
                perm.get("type") == "user"
                and perm.get("emailAddress", "").lower() == email_address.lower()
            ):
                logger.debug(f"Folder {folder_id} already shared with {email_address}")
                return True

        # User doesn't have access, proceed with sharing
        try:
            self._rate_limit()
            permission = {"type": "user", "role": "reader", "emailAddress": email_address}
            self.service.permissions().create(
                fileId=folder_id, body=permission, sendNotificationEmail=send_notification
            ).execute()
            logger.info(f"Shared folder {folder_id} with {email_address}")
            return True
        except HttpError as error:
            # Check if it's a duplicate permission error (already shared)
            if error.resp.status == 400 and "already has access" in str(error):
                logger.debug(f"Folder {folder_id} already shared with {email_address}")
                return True
            logger.error(
                f"An error occurred while sharing folder {folder_id} with {email_address}: {error}"
            )
            return False
        except Exception as e:
            logger.warning(f"Error sharing folder: {e}")
            return False

    def revoke_folder_access(self, folder_id: str, email_address: str) -> bool:
        """Revokes access to a folder for a specific user.

        Args:
            folder_id: Google Drive folder ID
            email_address: Email address of user to revoke access from

        Returns:
            True if successful or permission not found, False otherwise
        """
        # Validate folder ID
        if not self._validate_folder_id(folder_id):
            logger.error(f"Invalid folder ID format: {folder_id}")
            return False

        if not email_address or not email_address.strip():
            logger.warning(f"Invalid email address provided: {email_address}")
            return False

        email_address = email_address.strip().lower()

        # Get current permissions
        existing_permissions = self.get_folder_permissions(folder_id)
        permission_id = None

        for perm in existing_permissions:
            if perm.get("type") == "user" and perm.get("emailAddress", "").lower() == email_address:
                permission_id = perm.get("id")
                break

        if not permission_id:
            logger.debug(f"User {email_address} does not have access to folder {folder_id}")
            return True  # Already doesn't have access, consider it success

        # Revoke the permission
        try:
            self._rate_limit()
            self.service.permissions().delete(
                fileId=folder_id, permissionId=permission_id
            ).execute()
            logger.info(f"Revoked access to folder {folder_id} for {email_address}")
            return True
        except HttpError as error:
            if error.resp.status == 404:
                # Permission doesn't exist, that's fine
                logger.debug(f"Permission not found for {email_address} on folder {folder_id}")
                return True
            logger.error(
                f"An error occurred while revoking access to folder {folder_id} for {email_address}: {error}"
            )
            return False
        except Exception as e:
            logger.warning(f"Error revoking folder access: {e}")
            return False
