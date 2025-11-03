import logging
import os
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

    def _authenticate(self, credentials_file):
        """Authenticates with Google Drive API."""
        creds = None
        token_path = 'token.json'
        scopes = ['https://www.googleapis.com/auth/drive']

        if os.path.exists(token_path):
            creds = Credentials.from_authorized_user_file(token_path, scopes)
        
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(credentials_file, scopes)
                creds = flow.run_local_server(port=0)
            
            with open(token_path, 'w') as token:
                token.write(creds.to_json())
        
        return creds

    def create_folder(self, folder_name, parent_folder_id=None):
        """Creates a folder in Google Drive."""
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

    def upload_file(self, file_path, folder_id):
        """Uploads a file to a specific folder in Google Drive."""
        file_name = os.path.basename(file_path)
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
        """Shares a folder with a specific user."""
        try:
            permission = {
                'type': 'user',
                'role': 'reader',
                'emailAddress': email_address
            }
            self.service.permissions().create(fileId=folder_id, body=permission).execute()
            logger.info(f"Shared folder {folder_id} with {email_address}")
        except HttpError as error:
            logger.error(f"An error occurred while sharing folder {folder_id}: {error}")

