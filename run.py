"""Daily Dose of Streamers: one run = one finished Short on the channel.

    python run.py --next                 # next streamer in the rotation (what the schedule runs)
    python run.py --streamer kaicenat    # a specific streamer
    python run.py --next --dry-run       # make the video but don't upload it

Each run: latest finished VOD -> chat spikes -> best unused moment ->
download 30 s before and after -> subtitles -> branded vertical edit ->
title/description/hashtags/first comment -> upload.
"""
import argparse
import json
import os
import sys
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "engine"))
import editor, metadata, spikes, twitch  # noqa: E402

STATE = os.path.join(HERE, "state.json")
WORK = os.path.join(HERE, "work")
OUT = os.path.join(HERE, "output")


def load_state():
    if os.path.exists(STATE):
        with open(STATE) as f:
            return json.load(f)
    return {"turn": 0, "used": {}, "posted": []}


def save_state(state):
    with open(STATE, "w") as f:
        json.dump(state, f, indent=2)


def pick_moment(s, cfg, state):
    """Best chat spike on the newest finished VOD that we haven't clipped yet."""
    vods = twitch.latest_vods(s["login"], n=3)
    live = twitch.is_live(s["login"])
    if live:
        print(f"{s['name']} is live right now, using their last finished stream")
    for i, vod in enumerate(vods):
        if i == 0 and live:
            continue    # the newest VOD is still being recorded
        os.makedirs(WORK, exist_ok=True)
        chat_path = os.path.join(WORK, f"chat_{vod['id']}.json")
        if not os.path.exists(chat_path):
            print(f"Downloading chat for VOD {vod['id']} ...")
            twitch.download_chat(vod["id"], chat_path)
        chat = spikes.load_chat(chat_path)
        found = spikes.find_spikes(chat, top=10, min_score=cfg.get("min_spike_score", 3.0))
        used = state["used"].get(vod["id"], [])
        for sp in found:
            if all(abs(sp.t - u) > 120 for u in used):
                return vod, sp
    return None, None


def run_one(s, cfg, state, dry_run=False):
    vod, sp = pick_moment(s, cfg, state)
    if not vod:
        print(f"No new moments for {s['name']}.")
        return None
    start, end = spikes.clip_window(sp, before=cfg["before_s"], after=cfg["after_s"],
                                    chat_delay=cfg["chat_delay_s"], vod_len=vod["duration"])
    print(f"{s['name']}: chat spike at {sp.t:.0f}s (score {sp.score}), clipping {start:.0f}-{end:.0f}s")

    tag = f"{s['login']}_{vod['id']}_{int(sp.t)}"
    seg = os.path.join(WORK, f"seg_{tag}.mp4")
    twitch.download_segment(vod["id"], start, end, seg, quality=cfg.get("quality", "1080p60"))

    words, text = editor.transcribe(seg, cfg.get("whisper_model", "small"))
    stream_url = f"https://twitch.tv/{s['login']}"
    meta = metadata.write_metadata(s["name"], "Twitch", stream_url, text, sp.messages,
                                   channel=cfg["channel_name"], seed=tag)
    os.makedirs(OUT, exist_ok=True)
    out = os.path.join(OUT, f"{tag}.mp4")
    editor.render(seg, out, meta["hook"], s["name"], words, crop=s.get("crop", "center43"), workdir=WORK)
    with open(out.replace(".mp4", ".json"), "w") as f:
        json.dump(meta, f, indent=2)
    print(f"Rendered {out}\n  title: {meta['title']}\n  hook:  {meta['hook']}")

    video_id = None
    if not dry_run:
        import youtube
        video_id = youtube.upload(out, meta, privacy=cfg.get("privacy", "public"))
        print(f"Uploaded: https://youtube.com/shorts/{video_id}")
    state["used"].setdefault(vod["id"], []).append(sp.t)
    state["posted"].append({"streamer": s["name"], "vod": vod["id"], "t": sp.t,
                            "video_id": video_id, "title": meta["title"]})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--next", action="store_true")
    ap.add_argument("--streamer")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with open(os.path.join(HERE, "config.yaml")) as f:
        cfg = yaml.safe_load(f)
    streamers = [s for s in cfg["streamers"] if s.get("platform", "twitch") == "twitch" and s.get("enabled", True)]
    state = load_state()

    if args.streamer:
        order = [s for s in streamers if s["login"] == args.streamer]
    else:
        k = state["turn"] % len(streamers)
        order = streamers[k:] + streamers[:k]   # if one streamer has nothing new, try the next
    for s in order:
        try:
            if run_one(s, cfg, state, dry_run=args.dry_run):
                break
        except Exception as e:  # one broken streamer shouldn't stop the day
            print(f"Failed for {s['name']}: {e}")
    state["turn"] = state["turn"] + 1
    save_state(state)


if __name__ == "__main__":
    main()
