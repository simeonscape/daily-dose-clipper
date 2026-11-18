"""Write channel.json: the channel's live numbers and today's Shorts.

Run by the "Channel stats" workflow every 30 minutes. It only reads from
YouTube (about 3 quota units a run, out of 10,000 a day) using the same
secrets as the uploader, so no new keys are needed.

channel.json is public in this repo, so apps such as School OS can show the
channel without any API key or password of their own. It holds only numbers
that are already public on YouTube.
"""
import json
import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import yaml

from engine.youtube import client

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "channel.json")


def main():
    with open(os.path.join(HERE, "config.yaml")) as f:
        cfg = yaml.safe_load(f) or {}
    tz = ZoneInfo(cfg.get("post_timezone", "America/New_York"))
    yt = client()

    ch = yt.channels().list(part="snippet,statistics,contentDetails", mine=True).execute()["items"][0]
    st = ch["statistics"]
    uploads = ch["contentDetails"]["relatedPlaylists"]["uploads"]

    items = yt.playlistItems().list(part="contentDetails", playlistId=uploads, maxResults=50).execute()["items"]
    ids = [i["contentDetails"]["videoId"] for i in items]
    videos = []
    if ids:
        resp = yt.videos().list(part="snippet,statistics,status,contentDetails", id=",".join(ids)).execute()
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

    now = datetime.now(timezone.utc)
    today = now.astimezone(tz).date().isoformat()
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
    print(f"{data['channel']['title']}: {data['channel']['subscribers']} subscribers, "
          f"{len(data['today_videos'])} Shorts today")


if __name__ == "__main__":
    main()
