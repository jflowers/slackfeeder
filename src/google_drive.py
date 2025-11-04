import logging
import os
import shutil
import fcntl
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

logger = logging.getLogger(__name__)

class GoogleDriveClient:
    def __init__(self, credentials_file):
        self.creds = self._authenticate(credentials_file)
        self.service = build('drive', 'v3', credentials=self.creds)

    def _escape_drive_query_string(self, value):
        """Properly escape strings for Google Drive API queries.
        
        Args:
            value: String to escape
            
        Returns:
            Escaped string safe for use in Drive API queries
        """
        if not value:
            return ""
        # Escape backslashes first
        escaped = value.replace("\\", "\\\\")
        # Escape single quotes
        escaped = escaped.replace("'", "\\'")
        return escaped

    def _save_token_safely(self, token_path, creds):
        """Safely write token file with locking to prevent corruption.
        
        Args:
            token_path: Path to token file
            creds: Credentials object to save
        """
        temp_path = token_path + '.tmp'
        try:
            # Ensure directory exists
            token_dir = os.path.dirname(token_path) if os.path.dirname(token_path) else '.'
            os.makedirs(token_dir, exist_ok=True)
            
            with open(temp_path, 'w') as f:
                # Lock file for exclusive write
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                    f.write(creds.to_json())
                    f.flush()
                    os.fsync(f.fileno())  # Ensure data is written to disk
                finally:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
            
            # Atomic move
            shutil.move(temp_path, token_path)
            logger.debug(f"Token saved successfully to {token_path}")
        except Exception as e:
            logger.error(f"Failed to save token: {e}")
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except Exception:
                    pass
            raise

    def _authenticate(self, credentials_file):
        """Authenticates with Google Drive API."""
        creds = None
        # Use configurable token path, default to current directory
        token_path = os.getenv('GOOGLE_DRIVE_TOKEN_FILE', 'token.json')
        scopes = ['https://www.googleapis.com/auth/drive']

        if os.path.exists(token_path):
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

    def find_folder(self, folder_name, parent_folder_id=None):
        """Finds an existing folder by name in Google Drive.
        
        Args:
            folder_name: Name of the folder to find
            parent_folder_id: Optional parent folder ID to search within
            
        Returns:
            Folder ID if found, None otherwise
        """
        escaped_name = self._escape_drive_query_string(folder_name)
        query = f"name='{escaped_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
        if parent_folder_id:
            query += f" and '{parent_folder_id}' in parents"
        
        try:
            results = self.service.files().list(
                q=query,
                fields='files(id, name)',
                pageSize=1
            ).execute()
            items = results.get('files', [])
            if items:
                return items[0]['id']
        except HttpError as error:
            logger.warning(f"Error searching for folder '{folder_name}': {error}")
        return None

    def create_folder(self, folder_name, parent_folder_id=None):
        """Creates a folder in Google Drive, or returns existing folder if found."""
        # First check if folder already exists
        existing_folder_id = self.find_folder(folder_name, parent_folder_id)
        if existing_folder_id:
            logger.info(f"Found existing folder '{folder_name}' with ID: {existing_folder_id}")
            return existing_folder_id
        
        file_metadata = {
            'name': folder_name,
            'mimeType': 'application/vnd.google-apps.folder'
        }
        if parent_folder_id:
            file_metadata['parents'] = [parent_folder_id]
        
        try:
            folder = self.service.files().create(body=file_metadata, fields='id').execute()
            logger.info(f"Created folder '{folder_name}' with ID: {folder.get('id')}")
            return folder.get('id')
        except HttpError as error:
            logger.error(f"An error occurred while creating folder '{folder_name}': {error}")
            return None

    def upload_file(self, file_path, folder_id, overwrite=True):
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
        
        file_name = os.path.basename(file_path)
        
        # Check if file already exists
        if overwrite:
            escaped_file_name = self._escape_drive_query_string(file_name)
            query = f"name='{escaped_file_name}' and '{folder_id}' in parents and trashed=false"
            try:
                results = self.service.files().list(
                    q=query,
                    fields='files(id, name)',
                    pageSize=1
                ).execute()
                existing_files = results.get('files', [])
                if existing_files:
                    # Delete existing file
                    try:
                        existing_file_id = existing_files[0]['id']
                        self.service.files().delete(fileId=existing_file_id).execute()
                        logger.info(f"Deleted existing file '{file_name}' before uploading new version")
                    except HttpError as error:
                        logger.error(f"Failed to delete existing file '{file_name}': {error}")
                        if overwrite:
                            # If overwrite is requested and deletion fails, return None
                            return None
            except HttpError as error:
                logger.warning(f"Error checking for existing file '{file_name}': {error}")
        
        media = MediaFileUpload(file_path, mimetype='text/plain')
        file_metadata = {
            'name': file_name,
            'parents': [folder_id]
        }
        
        try:
            file = self.service.files().create(body=file_metadata, media_body=media, fields='id').execute()
            logger.info(f"Uploaded file '{file_name}' with ID: {file.get('id')}")
            return file.get('id')
        except HttpError as error:
            logger.error(f"An error occurred while uploading file '{file_name}': {error}")
            return None

    def share_folder(self, folder_id, email_address):
        """Shares a folder with a specific user.
        
        Args:
            folder_id: Google Drive folder ID to share
            email_address: Email address of user to share with
            
        Returns:
            True if successful, False otherwise
        """
        if not email_address or not email_address.strip():
            logger.warning(f"Invalid email address provided: {email_address}")
            return False
        
        try:
            permission = {
                'type': 'user',
                'role': 'reader',
                'emailAddress': email_address.strip()
            }
            self.service.permissions().create(fileId=folder_id, body=permission).execute()
            logger.info(f"Shared folder {folder_id} with {email_address}")
            return True
        except HttpError as error:
            # Check if it's a duplicate permission error (already shared)
            if error.resp.status == 400 and 'already has access' in str(error):
                logger.debug(f"Folder {folder_id} already shared with {email_address}")
                return True
            logger.error(f"An error occurred while sharing folder {folder_id} with {email_address}: {error}")
            return False

