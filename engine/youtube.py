"""Upload a Short to YouTube and post the first comment.

Needs YT_CLIENT_ID, YT_CLIENT_SECRET and YT_REFRESH_TOKEN (get the token once
with `python setup_youtube.py` on any computer).

Quota: YouTube gives 10,000 units a day. An upload costs 1,600 and a comment
50, so 5 Shorts plus 5 comments a day (8,250) fits.

Note: YouTube's API can post a comment but cannot pin it. Pin it in the
YouTube Studio app if you want it at the top.
"""
import os
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

SCOPES = ["https://www.googleapis.com/auth/youtube.upload",
          "https://www.googleapis.com/auth/youtube.force-ssl"]


def client():
    creds = Credentials(
        None,
        refresh_token=os.environ["YT_REFRESH_TOKEN"],
        client_id=os.environ["YT_CLIENT_ID"],
        client_secret=os.environ["YT_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def upload(path, meta, privacy="public"):
    yt = client()
    tags = [h.lstrip("#") for h in meta["hashtags"]]
    description = meta["description"].strip() + "\n\n" + " ".join(meta["hashtags"])
    body = {
        "snippet": {
            "title": meta["title"],
            "description": description,
            "tags": tags,
            "categoryId": "24",          # Entertainment
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(path, mimetype="video/mp4", resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    video_id = resp["id"]

    if meta.get("first_comment") and privacy == "public":
        try:
            yt.commentThreads().insert(part="snippet", body={
                "snippet": {"videoId": video_id,
                            "topLevelComment": {"snippet": {"textOriginal": meta["first_comment"]}}}
            }).execute()
        except Exception as e:
            # Happens while YouTube keeps uploads private (before the API audit).
            print(f"Couldn't post the first comment ({e}). Post it yourself: {meta['first_comment']}")
    return video_id
