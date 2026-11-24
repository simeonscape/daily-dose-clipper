"""One-time: sign in to the YouTube channel and print the refresh token.

1. In Google Cloud Console, create a project, enable "YouTube Data API v3",
   and create an OAuth client ID of type "Desktop app". Download it as
   client_secret.json next to this file.
2. pip install google-auth-oauthlib
3. python setup_youtube.py   (a browser window opens; pick the
   channel you post to)
4. Copy the three values it prints into your GitHub repo secrets.
"""
import json
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.force-ssl"]

flow = InstalledAppFlow.from_client_secrets_file("client_secret.json", SCOPES)
creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
info = json.load(open("client_secret.json"))["installed"]
print("\nAdd these as GitHub secrets:\n")
print("YT_CLIENT_ID     =", info["client_id"])
print("YT_CLIENT_SECRET =", info["client_secret"])
print("YT_REFRESH_TOKEN =", creds.refresh_token)
