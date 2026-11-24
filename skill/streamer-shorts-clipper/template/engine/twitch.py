"""Twitch side: find a streamer's clips, VODs and live streams (Helix API),
and download chat and video with TwitchDownloaderCLI
(https://github.com/lay295/TwitchDownloader).

A stream that is live right now already has a VOD that keeps growing, so
clips, chat and video from earlier in the current stream work the same way
as for a finished one. That is how the moments watcher.py signals are clipped.

Needs TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET from a free app at
https://dev.twitch.tv/console/apps (used only to list clips, VODs and streams).
"""
import os
import re
import shutil
import subprocess
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

HELIX = "https://api.twitch.tv/helix"
CLI = os.environ.get("TWITCHDOWNLOADER_CLI", shutil.which("TwitchDownloaderCLI") or "./TwitchDownloaderCLI")
FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
_token = None

# Twitch's API sometimes hiccups; retry a few times before giving up
_http = requests.Session()
_http.mount("https://", HTTPAdapter(max_retries=Retry(total=4, backoff_factor=1.5,
                                                      status_forcelist=(429, 500, 502, 503, 504),
                                                      allowed_methods=None)))


def _get(path, **params):
    r = _http.get(f"{HELIX}/{path}", params=params, headers=_headers(), timeout=30)
    r.raise_for_status()
    return r.json()["data"]


def _run(cmd, tries=3):
    """TwitchDownloaderCLI, retried: downloads fail now and then on GitHub's runners."""
    for i in range(tries):
        try:
            return subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError:
            if i == tries - 1:
                raise
            time.sleep(5 * (i + 1))


def _headers():
    global _token
    if _token is None:
        r = _http.post("https://id.twitch.tv/oauth2/token", data={
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
        data = _get("users", login=login)
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
    clips = _get("clips", broadcaster_id=user_id(login), first=n,
                 started_at=(now - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                 ended_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"))
    clips.sort(key=lambda c: -c["view_count"])
    return clips


def download_clip(clip_id, out):
    _run([CLI, "clipdownload", "--id", str(clip_id), "-o", out, "--ffmpeg-path", FFMPEG])
    return out


def download_chat_window(vod_id, start, end, out):
    _run([CLI, "chatdownload", "--id", str(vod_id), "-b", str(int(start)), "-e", str(int(end)), "-o", out])
    return out


def latest_vods(login, n=3):
    data = _get("videos", user_id=user_id(login), type="archive", first=n)
    return [{"id": x["id"], "url": x["url"], "title": x["title"], "created_at": x["created_at"],
             "stream_id": x.get("stream_id"), "duration": parse_duration(x["duration"])} for x in data]


def live_streams(logins):
    """{login: stream} for the ones live right now (one API call for up to 100)."""
    if not logins:
        return {}
    r = _http.get(f"{HELIX}/streams", params=[("user_login", l) for l in logins][:100],
                  headers=_headers(), timeout=30)
    r.raise_for_status()
    return {x["user_login"].lower(): x for x in r.json()["data"]}


def is_live(login):
    return login.lower() in live_streams([login])


def live_vod(login, stream=None):
    """The VOD being recorded for the current stream, or None (VODs off, or sub-only)."""
    stream = stream or live_streams([login]).get(login.lower())
    if not stream:
        return None
    for v in latest_vods(login, n=2):
        if v.get("stream_id") == stream["id"] or v["created_at"] >= stream["started_at"]:
            return v
    return None


def download_chat(vod_id, out):
    _run([CLI, "chatdownload", "--id", str(vod_id), "-o", out])
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
    _run(base)
    return out
