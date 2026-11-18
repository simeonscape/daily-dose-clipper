"""Posting times: when each day's Shorts go live.

The workflow runs every hour. Each run calls `python slots.py`, which prints the
next posting time that is coming up soon and has no Short yet (or nothing).
If it prints a time, the run makes one Short, waits until that time, and posts.

Needs only pyyaml, so the hourly check stays quick.
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "state.json")

EARLY = timedelta(minutes=80)   # start this long before a posting time, at most
LATE = timedelta(minutes=45)    # if GitHub starts the run late, still post up to this long after


def quota_day(when=None):
    """YouTube's daily API quota resets at midnight Pacific time."""
    return (when or datetime.now(timezone.utc)).astimezone(ZoneInfo("America/Los_Angeles")).date().isoformat()


def posted_today(state, when=None):
    # deleted Shorts still count: deleting a video doesn't give the quota back
    today = quota_day(when)
    return sum(1 for p in state["posted"] if p.get("video_id") and p.get("quota_day", p.get("day")) == today)


def slot_times(cfg, now):
    """Posting times for yesterday, today and tomorrow, in UTC."""
    tz = ZoneInfo(cfg.get("post_timezone", "America/New_York"))
    local = now.astimezone(tz).date()
    out = []
    for d in (local - timedelta(days=1), local, local + timedelta(days=1)):
        for hm in cfg.get("post_times", []):
            h, m = (int(x) for x in str(hm).split(":"))
            out.append(datetime(d.year, d.month, d.day, h, m, tzinfo=tz).astimezone(timezone.utc))
    return sorted(out)


def due_slot(cfg, state, now=None):
    """The posting time this run should fill, or None."""
    now = now or datetime.now(timezone.utc)
    filled = {p["slot"] for p in state["posted"] if p.get("slot") and p.get("video_id")}
    for t in slot_times(cfg, now):
        if now - LATE <= t <= now + EARLY and t.isoformat() not in filled:
            if posted_today(state, t) >= cfg.get("max_uploads_per_day", 6):
                return None      # that day's YouTube quota is used up
            return t
    return None


def load():
    with open(os.path.join(HERE, "config.yaml")) as f:
        cfg = yaml.safe_load(f)
    state = {"posted": []}
    if os.path.exists(STATE):
        with open(STATE) as f:
            state = json.load(f)
    return cfg, state


if __name__ == "__main__":
    cfg, state = load()
    t = due_slot(cfg, state)
    if t:
        print(t.isoformat())
    else:
        print("No posting time coming up.", file=sys.stderr)
