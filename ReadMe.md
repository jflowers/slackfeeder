**Important Prerequisites:**

1. **Python & slack\_sdk:** Make sure you have Python installed. You'll need to install the official Slack SDK: pip install slack\_sdk.  
2. **Slack App:** You need to create a Slack app ([https://api.slack.com/apps](https://api.slack.com/apps)). You must have permission within your workspace to do this.  
3. **Permissions (Scopes):** In your app's settings under "OAuth & Permissions", add the conversations:history scope to "Bot Token Scopes". This allows the app to read message history. Depending on the channel type, you might implicitly need related scopes like channels:read, groups:read, im:read, or mpim:read which conversations:history generally covers, but it's good practice to ensure the bot can access the relevant conversation type.  
4. **Bot Token:** After adding scopes, install (or reinstall) the app to your workspace. Copy the "Bot User OAuth Token" (it starts with xoxb-). **Treat this token like a password\!**  
5. **Channel ID:** Find the ID of the channel you want to export. You can usually find this in the Slack URL when viewing the channel (e.g., C0123ABC456).  
6. **Invite Bot:** If the channel is private, you **must invite the bot user** associated with your Slack app into that channel.

**How to Use:**

1. **Replace Placeholders:** Open export\_slack\_history.py and replace "YOUR\_SLACK\_BOT\_TOKEN\_HERE" with your actual Bot User OAuth Token and "YOUR\_CHANNEL\_ID\_HERE" with the target channel's ID.  
2. **Security:** For better security, avoid hardcoding the token. Use environment variables instead (as commented out in the script).  
3. **Run:** Execute the script from your terminal: python export\_slack\_history.py  
4. **Output:** It will fetch messages page by page (respecting rate limits with a small delay) and save all messages chronologically into a JSON file named \<CHANNEL\_ID\>\_history.json.

**Important Considerations:**

* **Authorization:** As the script's warning states, ensure you are authorized to export this data. Respect privacy and your organization's policies.  
* **Rate Limits:** Slack enforces rate limits. The script includes a basic delay (REQUEST\_DELAY\_SECONDS). If you encounter ratelimited errors, you may need to increase this delay or implement more robust backoff logic.  
* **Large Histories:** Exporting very large channel histories can take a significant amount of time and generate large files.  
* **Threads:** This script fetches the main channel messages. Messages within threads are retrieved using a different API method (conversations.replies) for each thread parent message. Modifying the script to fetch all thread replies would add complexity.  
* **Error Handling:** The script includes basic error handling, but you might want to enhance it depending on your needs (e.g., retrying specific errors).