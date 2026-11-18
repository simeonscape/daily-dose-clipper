"""Daily Dose of Streamers: one run = 1 to 5 finished Shorts on the channel.

    python run.py                        # the most viral moments across all streamers
    python run.py --clips 3              # make 3 Shorts this run
    python run.py --streamer kaicenat    # only look at one streamer
    python run.py --clips 5 --dry-run    # make the videos but don't upload them

Each run: every streamer's latest finished VODs -> chat spikes, ranked by how
hard chat spammed LOL and CLIP IT -> the top moments nobody has posted yet ->
download 30 s before and after -> subtitles -> branded vertical edit ->
title/description/hashtags/first comment -> upload.
"""
import argparse
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "engine"))
import editor, layout, metadata, spikes, twitch  # noqa: E402

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


def candidates(s, cfg, state):
    """Unused chat spikes from a streamer's latest finished VODs."""
    vods = twitch.latest_vods(s["login"], n=3)
    if not vods:
        print(f"{s['name']}: no Twitch VODs found")
        return []
    if twitch.is_live(s["login"]):
        print(f"{s['name']} is live right now, using their last finished stream")
        vods = vods[1:]          # the newest VOD is still being recorded
    out = []
    os.makedirs(WORK, exist_ok=True)
    for vod in vods[:cfg.get("vods_per_streamer", 2)]:
        chat_path = os.path.join(WORK, f"chat_{vod['id']}.json")
        if not os.path.exists(chat_path):
            print(f"Downloading chat for {s['name']}, VOD {vod['id']} ...")
            twitch.download_chat(vod["id"], chat_path)
        chat = spikes.load_chat(chat_path)
        used = state["used"].get(vod["id"], [])
        for sp in spikes.find_spikes(chat, top=10, min_score=cfg.get("min_spike_score", 3.0)):
            if all(abs(sp.t - u) > 120 for u in used):
                out.append((sp.viral, s, vod, sp))
    return out


def posted_today(state):
    today = datetime.now(timezone.utc).date().isoformat()
    return sum(1 for p in state["posted"] if p.get("video_id") and p.get("day") == today)


def prune_chats(keep_days=4):
    if not os.path.isdir(WORK):
        return
    cutoff = time.time() - keep_days * 86400
    for f in os.listdir(WORK):
        path = os.path.join(WORK, f)
        if f.startswith("chat_") and os.path.getmtime(path) < cutoff:
            os.remove(path)


def run_one(s, vod, sp, cfg, state, dry_run=False):
    start, end = spikes.clip_window(sp, before=cfg["before_s"], after=cfg["after_s"],
                                    chat_delay=cfg["chat_delay_s"], vod_len=vod["duration"])
    print(f"\n== {s['name']}: spike at {sp.t:.0f}s, viral {sp.viral} (LOL {sp.lol}, CLIP IT {sp.clip}), "
          f"clipping {start:.0f}-{end:.0f}s")

    tag = f"{s['login']}_{vod['id']}_{int(sp.t)}"
    seg = os.path.join(WORK, f"seg_{tag}.mp4")
    twitch.download_segment(vod["id"], start, end, seg, quality=cfg.get("quality", "1080p60"))

    words, text = editor.transcribe(seg, cfg.get("whisper_model", "small"))
    stream_url = f"https://twitch.tv/{s['login']}"
    meta = metadata.write_metadata(s["name"], "Twitch", stream_url, text, sp.messages,
                                   channel=cfg["channel_name"], seed=tag)
    os.makedirs(OUT, exist_ok=True)
    out = os.path.join(OUT, f"{tag}.mp4")
    lay = layout.find_layout(seg, override=s.get("cam"), debug_path=out.replace(".mp4", "_camera.jpg"))
    if s.get("layout") in ("cam", "game"):
        lay["mode"] = s["layout"]
    print(f"Layout: {lay['mode']} ({lay['how']})")
    editor.render(seg, out, meta["hook"], s["name"], words, lay, login=s["login"], workdir=WORK)
    with open(out.replace(".mp4", ".json"), "w") as f:
        json.dump(meta, f, indent=2)
    print(f"Rendered {out}\n  title: {meta['title']}\n  hook:  {meta['hook']}")

    video_id = None
    if not dry_run:
        import youtube
        video_id = youtube.upload(out, meta, privacy=cfg.get("privacy", "public"))
        print(f"Uploaded: https://youtube.com/shorts/{video_id}")
    state["used"].setdefault(vod["id"], []).append(sp.t)
    state["posted"].append({"streamer": s["name"], "vod": vod["id"], "t": sp.t, "viral": sp.viral,
                            "video_id": video_id, "title": meta["title"],
                            "day": datetime.now(timezone.utc).date().isoformat()})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clips", type=int, help="how many Shorts to make (default: clips_per_run in config.yaml)")
    ap.add_argument("--streamer", help="Twitch login, or 'all' (default)")
    ap.add_argument("--dry-run", action="store_true")
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
    want = args.clips or cfg.get("clips_per_run", 1)

    if not args.dry_run:
        left = cfg.get("max_uploads_per_day", 6) - posted_today(state)
        if left <= 0:
            print("Already posted today's maximum (YouTube's daily upload quota). Nothing to do.")
            return
        if want > left:
            print(f"Only {left} more upload(s) fit in today's YouTube quota, making {left}.")
            want = left

    prune_chats()
    pool, errors = [], 0
    for s in streamers:
        try:
            pool += candidates(s, cfg, state)
        except Exception as e:  # one broken streamer shouldn't stop the day
            errors += 1
            print(f"Couldn't read {s['name']}'s streams: {e}")
            traceback.print_exc()
    pool.sort(key=lambda c: -c[0])
    print(f"\nTop moments across {len(streamers)} streamer(s):")
    for v, s, vod, sp in pool[:10]:
        print(f"  {v:7.2f}  {s['name']:<11} VOD {vod['id']} at {sp.t:6.0f}s  LOL {sp.lol:4d}  CLIP IT {sp.clip:3d}")

    cap = cfg.get("max_per_streamer", 2)
    made, per = [], {}
    for v, s, vod, sp in pool:
        if len(made) >= want:
            break
        if per.get(s["login"], 0) >= cap:
            continue
        if any(m[1]["id"] == vod["id"] and abs(m[2].t - sp.t) < 120 for m in made):
            continue
        try:
            if run_one(s, vod, sp, cfg, state, dry_run=args.dry_run):
                made.append((s, vod, sp))
                per[s["login"]] = per.get(s["login"], 0) + 1
                if not args.dry_run:
                    save_state(state)     # save after each upload, in case a later one fails
        except Exception as e:  # try the next-best moment instead
            errors += 1
            print(f"Failed for {s['name']}: {e}")
            traceback.print_exc()

    print(f"\nMade {len(made)} of {want} Short(s): " + ", ".join(s["name"] for s, _, _ in made))
    if args.dry_run:
        print("Dry run: nothing uploaded, so the list of posted clips stays as it was.")
    else:
        save_state(state)
    if not made and errors:
        sys.exit("No Short was made because of the errors above.")


if __name__ == "__main__":
    main()
