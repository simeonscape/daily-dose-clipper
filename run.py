"""Daily Dose of Streamers: one run = 1 to 5 finished Shorts, one per streamer.

    python run.py                        # clips_per_run Shorts (config.yaml)
    python run.py --clips 5              # one Short for each of the 5 streamers
    python run.py --streamer kaicenat    # only this streamer
    python run.py --clips 5 --dry-run    # make the videos but don't upload them
    python run.py --slot 2026-11-19T17:00:00+00:00   # one Short, posted at that time

Every day the hourly workflow posts one Short at each of the post_times in
config.yaml (see slots.py), so they go out spread over the day.

How a moment is picked, per streamer (engine/moments.py, engine/virality.py):
  1. Candidates: their most-viewed Twitch clips from the last few days (clips
     of the same moment are added together; clips from a stream that is live
     right now count too). With no fresh clips: chat spikes in their latest
     stream, live or finished.
  2. Ranking: the top few get chat fetched around them, and every candidate
     gets one virality score from clip views, how many people clipped it, how
     hard chat exploded, laughter, CLIP IT spam and how recent it is.
  3. The window: a wider piece around the winner is downloaded, and the Short
     is centred on its loudest, most chat-heavy point, cut at quiet spots.
A moment that was already posted (same part of the stream, same clip, or same
clip title) is skipped. With fewer than 5 Shorts, the streamers who were posted
longest ago go first, so everyone gets their turn.

Then: blur explicit spots -> subtitles -> layout picked for the clip, following
the action -> title/description/hashtags/first comment -> upload.
Live streams as they happen: see live.py.
"""
import argparse
import os
import sys
import time
import traceback
from datetime import datetime, timezone

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "engine"))
import moments, pipeline, safety, spikes, twitch  # noqa: E402
import state as st  # noqa: E402
from slots import posted_today, quota_day  # noqa: E402

WORK = os.path.join(HERE, "work")
OUT = os.path.join(HERE, "output")
PAD = 20          # extra seconds downloaded on each side, so the window can move


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
        if f.startswith("chat") and os.path.getmtime(path) < cutoff:
            os.remove(path)


def get_segment(m, cfg, tag):
    """Download the moment, cut to its best window. Returns (video path, chat lines)."""
    os.makedirs(WORK, exist_ok=True)
    seg = os.path.join(WORK, f"seg_{tag}.mp4")
    before, after = cfg["before_s"], cfg["after_s"]
    if m.vod:
        start = max(0.0, m.t - before - PAD)
        end = m.t + after + PAD
        if m.vod.get("duration"):
            end = min(end, m.vod["duration"])
        print(f"  downloading {start:.0f}-{end:.0f}s of VOD {m.vod['id']}{' (live)' if m.live else ''}")
        try:
            twitch.download_segment(m.vod["id"], start, end, seg, quality=cfg.get("quality"))
        except Exception as e:
            if not m.clip_id:
                raise
            # e.g. subscriber-only VODs: the clip itself is still public
            print(f"  can't download the VOD ({e}); using clip {m.clip_id} itself")
            twitch.download_clip(m.clip_id, seg)
            return seg, []
        chat = None
        try:
            chat_path = os.path.join(WORK, f"chatwin_{tag}.json")
            twitch.download_chat_window(m.vod["id"], start, end, chat_path)
            chat = spikes.load_chat(chat_path)
        except Exception as e:
            print(f"  (couldn't get chat for this moment: {e})")
        cut, a = pipeline.best_window(seg, m.t - start, cfg, chat=chat, seg_start=start)
        msgs = [b for t, b in (chat or []) if start + a <= t <= start + a + before + after + 8] or m.messages
        return cut, msgs
    print(f"  the stream's VOD is gone, using clip {m.clip_id} itself")
    twitch.download_clip(m.clip_id, seg)
    return seg, []


def run_one(s, m, cfg, state, dry_run=False, slot=None):
    print(f"\n== {s['name']}: {m.describe()}")
    tag = f"{s['login']}_{m.key.replace(':', '_')}_{int(m.t)}"
    seg, msgs = get_segment(m, cfg, tag)
    out, meta = pipeline.make_short(s, seg, msgs, cfg, tag, OUT, WORK, clip_title=m.title)

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
        st.mark_posted(state, s["name"], m, video_id, meta, slot=slot, quota_day=quota_day(),
                       extra={"layout": meta.get("layout")})
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
    state = st.load()
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
            found = moments.candidates(s, cfg, state, WORK)
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
                        st.save(state)    # save after each upload, in case a later one fails
                    break
            except safety.Unsafe as e:
                print(f"Skipping this moment for {s['name']}: {e}. Trying the next one.")
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
        st.save(state)
    if not made and errors:
        sys.exit("No Short was made because of the errors above.")


if __name__ == "__main__":
    main()
