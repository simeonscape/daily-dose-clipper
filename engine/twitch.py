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


_ids = {}


def user_id(login):
    if login not in _ids:
        u = requests.get(f"{HELIX}/users", params={"login": login}, headers=_headers(), timeout=30)
        u.raise_for_status()
        data = u.json()["data"]
        if not data:
            raise ValueError(f"Twitch user not found: {login}")
        _ids[login] = data[0]["id"]
    return _ids[login]


def top_clips(login, days=7, n=100):
    """The streamer's most-viewed clips from the last few days, most views first.

    Clips are what viewers chose to save and share, so their view counts are
    the best sign of which moments went viral."""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    r = requests.get(f"{HELIX}/clips", headers=_headers(), timeout=30, params={
        "broadcaster_id": user_id(login), "first": n,
        "started_at": (now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "ended_at": now.strftime("%Y-%m-%dT%H:%M:%SZ")})
    r.raise_for_status()
    clips = r.json()["data"]
    clips.sort(key=lambda c: -c["view_count"])
    return clips


def download_clip(clip_id, out):
    subprocess.run([CLI, "clipdownload", "--id", str(clip_id), "-o", out, "--ffmpeg-path", FFMPEG], check=True)
    return out


def download_chat_window(vod_id, start, end, out):
    subprocess.run([CLI, "chatdownload", "--id", str(vod_id), "-b", str(int(start)), "-e", str(int(end)),
                    "-o", out], check=True)
    return out


def latest_vods(login, n=3):
    h = _headers()
    v = requests.get(f"{HELIX}/videos", params={"user_id": user_id(login), "type": "archive", "first": n},
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


def download_segment(vod_id, start, end, out, quality=None):
    """Part of a VOD. With no quality given, TwitchDownloader takes the best (source)."""
    base = [CLI, "videodownload", "--id", str(vod_id), "-b", str(int(start)), "-e", str(int(end)),
            "-o", out, "--ffmpeg-path", FFMPEG]
    if quality:
        try:
            subprocess.run(base + ["-q", quality], check=True)
            return out
        except subprocess.CalledProcessError:
            pass
    subprocess.run(base, check=True)
    return out
