"""Daily Dose of Streamers: one run = 1 to 5 finished Shorts, one per streamer.

    python run.py                        # clips_per_run Shorts (config.yaml)
    python run.py --clips 5              # one Short for each of the 5 streamers
    python run.py --streamer kaicenat    # only this streamer
    python run.py --clips 5 --dry-run    # make the videos but don't upload them
    python run.py --slot 2026-11-19T17:00:00+00:00   # one Short, posted at that time

Every day the hourly workflow posts one Short at each of the post_times in
config.yaml (see slots.py), so they go out spread over the day.

How a moment is picked, per streamer:
  1. Their most-viewed Twitch clips from the last few days. Clips are what
     viewers saved and shared, so views are the best sign a moment went viral.
     Clips of the same moment are added together. A moment that was already
     posted (same part of the stream, same clip, or same clip title) is skipped,
     so a clip that stays popular for days isn't posted twice.
  2. If they have no recent clips: the biggest chat spike in their latest
     finished stream, weighted toward LOL spam and CLIP IT.
With fewer than 5 Shorts, the streamers who were posted longest ago go first,
so everyone gets their turn.

Then: 30 s before and after the moment -> subtitles -> branded vertical edit ->
title/description/hashtags/first comment -> upload.
"""
import argparse
import json
import os
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "engine"))
import editor, layout, metadata, spikes, twitch  # noqa: E402
from slots import posted_today, quota_day  # noqa: E402

STATE = os.path.join(HERE, "state.json")
WORK = os.path.join(HERE, "work")
OUT = os.path.join(HERE, "output")


@dataclass
class Moment:
    score: float                 # clip views (or chat spike score for the fallback)
    source: str                  # "clips" or "chat"
    vod: dict = None             # VOD it's in, if the VOD is still up
    t: float = 0.0               # seconds into the VOD
    clip_id: str = None          # for clips whose VOD is gone: download the clip itself
    title: str = ""              # title viewers gave the clip
    clips: int = 1               # how many clips were made of this moment
    clip_ids: list = field(default_factory=list)
    messages: list = field(default_factory=list)

    @property
    def key(self):
        return self.vod["id"] if self.vod else f"clip:{self.clip_id}"


def load_state():
    if os.path.exists(STATE):
        with open(STATE) as f:
            return json.load(f)
    return {"turn": 0, "used": {}, "used_clips": [], "posted": []}


def save_state(state):
    with open(STATE, "w") as f:
        json.dump(state, f, indent=2)


def is_used(state, key, t):
    return any(abs(t - u) < 90 for u in state["used"].get(key, []))


def _norm(title):
    return " ".join("".join(ch for ch in (title or "").lower() if ch.isalnum() or ch == " ").split())


def is_repeat(state, s, m):
    """Was this moment posted already? Same part of the stream, any of the same
    clips, or a clip with the same title (deleted Shorts don't count)."""
    if is_used(state, m.key, m.t):
        return True
    if set(m.clip_ids) & set(state.get("used_clips", [])):
        return True
    t = _norm(m.title)
    return bool(t) and any(_norm(p.get("clip_title")) == t for p in state["posted"]
                           if p["streamer"] == s["name"] and p.get("video_id") and not p.get("deleted"))


def clip_moments(s, cfg, state):
    """The streamer's recent clips, grouped by moment, most views first."""
    clips = twitch.top_clips(s["login"], days=cfg.get("clip_days", 7))
    vods = {}
    moments = []
    for c in clips:
        if c["view_count"] < cfg.get("min_clip_views", 200):
            continue
        if c.get("video_id") and c.get("vod_offset") is not None:
            # the moment is usually near the end of a clip
            t = c["vod_offset"] + 0.7 * c["duration"]
            same = next((m for m in moments if m.vod and m.vod["id"] == c["video_id"] and abs(m.t - t) < 40), None)
            if same:
                same.score += c["view_count"]; same.clips += 1; same.clip_ids.append(c["id"])
                continue
            vods.setdefault(c["video_id"], {"id": c["video_id"], "duration": None})
            moments.append(Moment(score=c["view_count"], source="clips", vod=vods[c["video_id"]], t=t,
                                  title=c["title"], clip_id=c["id"], clip_ids=[c["id"]]))
        else:
            moments.append(Moment(score=c["view_count"], source="clips", clip_id=c["id"], title=c["title"],
                                  clip_ids=[c["id"]]))
    moments.sort(key=lambda m: -m.score)
    fresh = [m for m in moments if not is_repeat(state, s, m)]
    if moments and moments[0] not in fresh:
        print(f"{s['name']}: top moment \"{moments[0].title}\" was already posted, picking the next one")
    return fresh


def chat_moments(s, cfg, state):
    """Fallback: chat spikes in the latest finished stream."""
    vods = twitch.latest_vods(s["login"], n=2)
    if vods and twitch.is_live(s["login"]):
        vods = vods[1:]          # the newest VOD is still being recorded
    if not vods:
        return []
    vod = vods[0]
    os.makedirs(WORK, exist_ok=True)
    chat_path = os.path.join(WORK, f"chat_{vod['id']}.json")
    if not os.path.exists(chat_path):
        print(f"Downloading chat for {s['name']}, VOD {vod['id']} ...")
        twitch.download_chat(vod["id"], chat_path)
    found = spikes.find_spikes(spikes.load_chat(chat_path), top=10, min_score=cfg.get("min_spike_score", 3.0))
    return [Moment(score=sp.viral, source="chat", vod=vod, t=sp.t, messages=sp.messages)
            for sp in found if not is_used(state, vod["id"], sp.t)]


def moments_for(s, cfg, state):
    try:
        found = clip_moments(s, cfg, state)
    except Exception as e:
        print(f"Couldn't read {s['name']}'s clips ({e}), using chat instead")
        found = []
    if found:
        print(f"{s['name']}: {len(found)} clipped moments, top: "
              + "; ".join(f"{m.score:,.0f} views ({m.clips} clips) \"{m.title}\"" for m in found[:3]))
        return found
    print(f"{s['name']}: no recent clips, looking at chat spikes")
    return chat_moments(s, cfg, state)


def last_posted(state, s):
    times = [p.get("at", p.get("day", "")) for p in state["posted"]
             if p.get("video_id") and not p.get("deleted") and p["streamer"] == s["name"]]
    return max(times) if times else ""


def prune_chats(keep_days=4):
    if not os.path.isdir(WORK):
        return
    cutoff = time.time() - keep_days * 86400
    for f in os.listdir(WORK):
        path = os.path.join(WORK, f)
        if f.startswith("chat_") and os.path.getmtime(path) < cutoff:
            os.remove(path)


def get_segment(m, cfg, tag):
    """Download the moment. Returns (video path, chat lines around it)."""
    os.makedirs(WORK, exist_ok=True)
    seg = os.path.join(WORK, f"seg_{tag}.mp4")
    if m.vod:
        start = max(0.0, m.t - cfg["chat_delay_s"] * (m.source == "chat") - cfg["before_s"])
        end = start + cfg["before_s"] + cfg["after_s"]
        if m.vod.get("duration"):
            end = min(end, m.vod["duration"])
        print(f"  clipping {start:.0f}-{end:.0f}s of VOD {m.vod['id']}")
        try:
            twitch.download_segment(m.vod["id"], start, end, seg, quality=cfg.get("quality"))
        except Exception as e:
            if not m.clip_id:
                raise
            # e.g. subscriber-only VODs: the clip itself is still public
            print(f"  can't download the VOD ({e}); using clip {m.clip_id} itself")
            twitch.download_clip(m.clip_id, seg)
            return seg, []
        msgs = m.messages
        if not msgs:
            try:
                chat_path = os.path.join(WORK, f"chatwin_{tag}.json")
                twitch.download_chat_window(m.vod["id"], start, end, chat_path)
                msgs = [b for _, b in spikes.load_chat(chat_path)]
            except Exception as e:
                print(f"  (couldn't get chat for this moment: {e})")
        return seg, msgs
    print(f"  the stream's VOD is gone, using clip {m.clip_id} itself")
    twitch.download_clip(m.clip_id, seg)
    return seg, []


def run_one(s, m, cfg, state, dry_run=False, slot=None):
    print(f"\n== {s['name']}: {m.score:,.0f} {'views' if m.source == 'clips' else 'chat score'}"
          + (f", \"{m.title}\"" if m.title else ""))
    tag = f"{s['login']}_{m.key.replace(':', '_')}_{int(m.t)}"
    seg, msgs = get_segment(m, cfg, tag)
    seg = editor.normalize(seg)

    words, text = editor.transcribe(seg, cfg.get("whisper_model", "small"))
    stream_url = f"https://twitch.tv/{s['login']}"
    meta = metadata.write_metadata(s["name"], "Twitch", stream_url, text, msgs,
                                   channel=cfg["channel_name"], seed=tag, clip_title=m.title)
    os.makedirs(OUT, exist_ok=True)
    out = os.path.join(OUT, f"{tag}.mp4")
    lay = layout.find_layout(seg, override=s.get("cam"), debug_path=out.replace(".mp4", "_camera.jpg"))
    if s.get("layout") in ("cam", "game"):
        lay["mode"] = s["layout"]
    print(f"  layout: {lay['mode']} ({lay['how']})")
    editor.render(seg, out, meta["hook"], s["name"], words, lay, login=s["login"], workdir=WORK)
    with open(out.replace(".mp4", ".json"), "w") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    print(f"  rendered {os.path.basename(out)}\n  title: {meta['title']}\n  hook:  {meta['hook']}")

    video_id = None
    if not dry_run:
        import youtube
        if slot:
            wait = (slot - datetime.now(timezone.utc)).total_seconds()
            if wait > 0:
                print(f"  waiting {wait / 60:.0f} min to post at {slot:%H:%M} UTC")
                time.sleep(wait)
        video_id = youtube.upload(out, meta, privacy=cfg.get("privacy", "public"))
        print(f"  uploaded: https://youtube.com/shorts/{video_id}")
        state["used"].setdefault(m.key, []).append(m.t)
        state.setdefault("used_clips", []).extend(m.clip_ids)
        state["posted"].append({"streamer": s["name"], "moment": m.key, "t": m.t, "score": m.score,
                                "video_id": video_id, "title": meta["title"],
                                "clip_title": m.title, "clip_ids": m.clip_ids,
                                "day": datetime.now(timezone.utc).date().isoformat(),
                                "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                "slot": slot.isoformat() if slot else None,
                                "quota_day": quota_day()})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clips", type=int, help="how many Shorts (default: clips_per_run in config.yaml)")
    ap.add_argument("--streamer", help="Twitch login, or 'all' (default)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--slot", help="posting time (ISO, from slots.py): make one Short and post it then")
    ap.add_argument("--next", action="store_true", help=argparse.SUPPRESS)   # old name, same as default
    args = ap.parse_args()

    with open(os.path.join(HERE, "config.yaml")) as f:
        cfg = yaml.safe_load(f)
    streamers = [s for s in cfg["streamers"] if s.get("platform", "twitch") == "twitch" and s.get("enabled", True)]
    if args.streamer and args.streamer != "all":
        streamers = [s for s in streamers if s["login"] == args.streamer]
        if not streamers:
            sys.exit(f"No enabled streamer with login {args.streamer} in config.yaml")
    state = load_state()
    want = min(args.clips or cfg.get("clips_per_run", 1), len(streamers))
    slot = None
    if args.slot:
        slot = datetime.fromisoformat(args.slot)
        if any(p.get("slot") == slot.isoformat() and p.get("video_id") for p in state["posted"]):
            print(f"A Short is already posted for {args.slot}. Nothing to do.")
            return
        want = 1

    if not args.dry_run:
        left = cfg.get("max_uploads_per_day", 6) - posted_today(state)
        if left <= 0:
            print("Already posted today's maximum (YouTube's daily upload quota). Nothing to do.")
            return
        if want > left:
            print(f"Only {left} more upload(s) fit in today's YouTube quota, making {left}.")
            want = left

    prune_chats()
    # streamers posted longest ago go first, so everyone gets turns
    order = sorted(streamers, key=lambda s: last_posted(state, s))
    made, errors = [], 0
    for s in order:
        if len(made) >= want:
            break
        try:
            found = moments_for(s, cfg, state)
        except Exception as e:
            errors += 1
            print(f"Couldn't read {s['name']}'s streams: {e}")
            traceback.print_exc()
            continue
        for m in found[:3]:                  # if the best one fails, try the next
            try:
                if run_one(s, m, cfg, state, dry_run=args.dry_run, slot=slot):
                    made.append(s["name"])
                    if not args.dry_run:
                        save_state(state)    # save after each upload, in case a later one fails
                    break
            except Exception as e:
                errors += 1
                print(f"Failed for {s['name']}: {e}")
                traceback.print_exc()
        else:
            print(f"Nothing usable for {s['name']} this time.")

    print(f"\nMade {len(made)} of {want} Short(s): " + ", ".join(made))
    if args.dry_run:
        print("Dry run: nothing uploaded, so the list of posted clips stays as it was.")
    else:
        save_state(state)
    if not made and errors:
        sys.exit("No Short was made because of the errors above.")


if __name__ == "__main__":
    main()
