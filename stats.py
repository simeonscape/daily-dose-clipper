"""Write channel.json: the channel's live numbers and today's Shorts.

Run by the "Channel stats" workflow every 30 minutes. It only reads from
YouTube (about 3 quota units a run, out of 10,000 a day) using the same
secrets as the uploader, so no new keys are needed.

channel.json is public in this repo, so apps such as School OS can show the
channel without any API key or password of their own. It holds only numbers
that are already public on YouTube.

history.json keeps one snapshot per day (the day's last run wins): channel
totals plus views, likes and comments for every Short, and a catalog of every
Short ever seen (even after it leaves the 20 in channel.json). Daily, weekly
and monthly summaries compare these snapshots. Normal runs read only the 50
newest uploads; the first run of each day reads every page once, so older
Shorts stay current without costing more than a few quota units a day.
"""
import json
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import yaml

from engine.youtube import client

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "channel.json")
HISTORY = os.path.join(HERE, "history.json")


def load_history():
    if os.path.exists(HISTORY):
        with open(HISTORY) as f:
            return json.load(f)
    return {"days": {}, "videos": {}}


def upload_ids(yt, playlist, all_pages):
    """Video ids in the uploads playlist, newest first: one page of 50, or all."""
    ids, token = [], None
    while True:
        resp = yt.playlistItems().list(part="contentDetails", playlistId=playlist,
                                       maxResults=50, pageToken=token).execute()
        ids += [i["contentDetails"]["videoId"] for i in resp["items"]]
        token = resp.get("nextPageToken")
        if not token or not all_pages:
            return ids


def update_history(history, data, videos, today):
    """Save today's snapshot. Shorts not read this run keep their last numbers."""
    days = history.setdefault("days", {})
    catalog = history.setdefault("videos", {})
    for v in videos:
        catalog[v["id"]] = {k: v[k] for k in ("title", "published", "day", "url", "streamer", "duration")}
    last = days[max(days)]["per_video"] if days else {}
    per_video = dict(last)
    per_video.update({v["id"]: [v["views"], v["likes"], v["comments"]] for v in videos})
    ch = data["channel"]
    days[today] = {
        "at": data["updated"],
        "subscribers": ch["subscribers"],
        "views": ch["views"],
        "videos": ch["videos"],
        "per_video": per_video,
    }


def main():
    with open(os.path.join(HERE, "config.yaml")) as f:
        cfg = yaml.safe_load(f) or {}
    tz = ZoneInfo(cfg.get("post_timezone", "America/New_York"))
    yt = client()

    ch = yt.channels().list(part="snippet,statistics,contentDetails", mine=True).execute()["items"][0]
    st = ch["statistics"]
    uploads = ch["contentDetails"]["relatedPlaylists"]["uploads"]

    now = datetime.now(timezone.utc)
    today = now.astimezone(tz).date().isoformat()
    history = load_history()
    ids = upload_ids(yt, uploads, all_pages=today not in history.get("days", {}))
    videos = []
    for start in range(0, len(ids), 50):
        resp = yt.videos().list(part="snippet,statistics,status,contentDetails",
                                id=",".join(ids[start:start + 50])).execute()
        for v in resp["items"]:
            s = v.get("statistics", {})
            videos.append({
                "id": v["id"],
                "title": v["snippet"]["title"],
                "published": v["snippet"]["publishedAt"],
                "thumbnail": f"https://i.ytimg.com/vi/{v['id']}/mqdefault.jpg",
                "url": f"https://youtube.com/shorts/{v['id']}",
                "privacy": v["status"]["privacyStatus"],
                "duration": v["contentDetails"]["duration"],
                "views": int(s.get("viewCount", 0)),
                "likes": int(s.get("likeCount", 0)),
                "comments": int(s.get("commentCount", 0)),
            })
    videos.sort(key=lambda v: v["published"], reverse=True)

    # Streamer names and upcoming slots come from the clipper's own records.
    state = {"posted": []}
    if os.path.exists(os.path.join(HERE, "state.json")):
        with open(os.path.join(HERE, "state.json")) as f:
            state = json.load(f)
    who = {p["video_id"]: p["streamer"] for p in state.get("posted", []) if p.get("video_id")}
    for v in videos:
        v["streamer"] = who.get(v["id"])

    for v in videos:
        v["day"] = datetime.fromisoformat(v["published"].replace("Z", "+00:00")).astimezone(tz).date().isoformat()

    data = {
        "updated": now.isoformat(timespec="seconds"),
        "timezone": str(tz),
        "today": today,
        "post_times": cfg.get("post_times", []),
        "channel": {
            "id": ch["id"],
            "title": ch["snippet"]["title"],
            "handle": ch["snippet"].get("customUrl", ""),
            "thumbnail": ch["snippet"]["thumbnails"].get("medium", ch["snippet"]["thumbnails"]["default"])["url"],
            "url": f"https://www.youtube.com/channel/{ch['id']}",
            "subscribers": int(st.get("subscriberCount", 0)),
            "subscribers_hidden": st.get("hiddenSubscriberCount", False),
            "views": int(st.get("viewCount", 0)),
            "videos": int(st.get("videoCount", 0)),
        },
        "today_videos": [v for v in videos if v["day"] == today],
        "recent": videos[:20],
    }
    with open(OUT, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    update_history(history, data, videos, today)
    with open(HISTORY, "w") as f:
        json.dump(history, f, ensure_ascii=False, separators=(",", ":"))
        f.write("\n")
    print(f"{data['channel']['title']}: {data['channel']['subscribers']} subscribers, "
          f"{len(data['today_videos'])} Shorts today")


if __name__ == "__main__":
    main()
