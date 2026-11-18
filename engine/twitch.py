"""Twitch side: find a streamer's latest VODs and download chat and video
with TwitchDownloaderCLI (https://github.com/lay295/TwitchDownloader).

Needs TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET from a free app at
https://dev.twitch.tv/console/apps (used only to list VODs).
"""
import os
import re
import shutil
import subprocess
import requests

HELIX = "https://api.twitch.tv/helix"
CLI = os.environ.get("TWITCHDOWNLOADER_CLI", shutil.which("TwitchDownloaderCLI") or "./TwitchDownloaderCLI")
FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
_token = None


def _headers():
    global _token
    if _token is None:
        r = requests.post("https://id.twitch.tv/oauth2/token", data={
            "client_id": os.environ["TWITCH_CLIENT_ID"],
            "client_secret": os.environ["TWITCH_CLIENT_SECRET"],
            "grant_type": "client_credentials",
        }, timeout=30)
        r.raise_for_status()
        _token = r.json()["access_token"]
    return {"Client-Id": os.environ["TWITCH_CLIENT_ID"], "Authorization": f"Bearer {_token}"}


def parse_duration(s):
    """'3h2m10s' -> seconds."""
    total = 0
    for n, unit in re.findall(r"(\d+)([hms])", s):
        total += int(n) * {"h": 3600, "m": 60, "s": 1}[unit]
    return total


def latest_vods(login, n=3):
    h = _headers()
    u = requests.get(f"{HELIX}/users", params={"login": login}, headers=h, timeout=30)
    u.raise_for_status()
    data = u.json()["data"]
    if not data:
        raise ValueError(f"Twitch user not found: {login}")
    v = requests.get(f"{HELIX}/videos", params={"user_id": data[0]["id"], "type": "archive", "first": n},
                     headers=h, timeout=30)
    v.raise_for_status()
    return [{"id": x["id"], "url": x["url"], "title": x["title"], "created_at": x["created_at"],
             "duration": parse_duration(x["duration"])} for x in v.json()["data"]]


def is_live(login):
    r = requests.get(f"{HELIX}/streams", params={"user_login": login}, headers=_headers(), timeout=30)
    r.raise_for_status()
    return bool(r.json()["data"])


def download_chat(vod_id, out):
    subprocess.run([CLI, "chatdownload", "--id", str(vod_id), "-o", out], check=True)
    return out


def download_segment(vod_id, start, end, out, quality="1080p60"):
    base = [CLI, "videodownload", "--id", str(vod_id), "-b", str(int(start)), "-e", str(int(end)),
            "-o", out, "--ffmpeg-path", FFMPEG]
    try:
        subprocess.run(base + ["-q", quality], check=True)
    except subprocess.CalledProcessError:
        subprocess.run(base, check=True)   # fall back to the best quality available
    return out
