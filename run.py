"""Daily Dose of Streamers: one run = 1 to 5 finished Shorts, one per streamer.

    python run.py                        # clips_per_run Shorts (config.yaml)
    python run.py --clips 5              # one Short for each of the 5 streamers
    python run.py --streamer kaicenat    # only this streamer
    python run.py --clips 5 --dry-run    # make the videos but don't upload them
    python run.py --slot 2026-11-19T17:00:00+00:00   # one Short, posted at that time
    python run.py --live kaicenat --vod 123 --t 5400  # a live moment the watcher signaled

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
Live streams as they happen: watcher.py signals moments, run.py --live clips them.
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
import ai, moments, pipeline, safety, spikes, twitch  # noqa: E402
import state as st  # noqa: E402
from slots import done_today, enabled_streamers, posted_today, quota_day  # noqa: E402

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


def context(cfg):
    """Seconds downloaded before and after a moment, for the judge to look at."""
    sc = cfg.get("smart", {}) or {}
    if sc.get("enabled", True):
        return float(sc.get("context_before", 75)), float(sc.get("context_after", 60))
    return cfg["before_s"] + PAD, cfg["after_s"] + PAD


def get_segment(m, cfg, tag, streamer="", info=None):
    """Download the moment, cut to its best part. Returns (video path, chat lines, notes)."""
    os.makedirs(WORK, exist_ok=True)
    seg = os.path.join(WORK, f"seg_{tag}.mp4")
    before, after = cfg["before_s"], cfg["after_s"]
    if m.vod:
        b, a = context(cfg)
        start = max(0.0, m.t - b)
        end = m.t + a
        if m.clip_ranges:     # make sure every viewer clip of it is inside, with some room
            start = max(0.0, min(start, min(r[0] for r in m.clip_ranges) - 15))
            end = max(end, max(r[1] for r in m.clip_ranges) + 15)
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
            return seg, [], {}
        chat = None
        try:
            chat_path = os.path.join(WORK, f"chatwin_{tag}.json")
            twitch.download_chat_window(m.vod["id"], start, end, chat_path)
            chat = spikes.load_chat(chat_path)
        except Exception as e:
            print(f"  (couldn't get chat for this moment: {e})")
        cut, a, notes = pipeline.best_window(seg, m.t - start, cfg, chat=chat, seg_start=start,
                                             clip_ranges=m.clip_ranges, streamer=streamer, workdir=WORK,
                                             info=info)
        msgs = [b for t, b in (chat or []) if start + a <= t <= start + a + before + after + 8] or m.messages
        return cut, msgs, notes
    print(f"  the stream's VOD is gone, using clip {m.clip_id} itself")
    twitch.download_clip(m.clip_id, seg)
    return seg, [], {}


def run_one(s, m, cfg, state, dry_run=False, slot=None):
    print(f"\n== {s['name']}: {m.describe()}")
    tag = f"{s['login']}_{m.key.replace(':', '_')}_{int(m.t)}"
    recent = ai.recent_titles(state, s["name"]) + ai.recent_titles(state, n=6)
    recent = list(dict.fromkeys(recent))
    info = {"streamer": s["name"], "channel": cfg["channel_name"], "clip_title": m.title,
            "recent_titles": recent,
            "forced_layout": s.get("layout") or (cfg.get("layout", {}) or {}).get("force") or s.get("cam")}
    seg, msgs, notes = get_segment(m, cfg, tag, streamer=s["name"], info=info)
    out, meta = pipeline.make_short(s, seg, msgs, cfg, tag, OUT, WORK, clip_title=m.title, notes=notes,
                                    recent=recent, recent_hooks=ai.recent_titles(state, field="hook", n=25))
    if notes.get("summary"):
        meta["what_happened"] = notes["summary"]

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
                       extra={"layout": meta.get("layout"), "hook": meta.get("hook"),
                              "written_by": meta.get("written_by", "built-in")})
    return out


def clip_live(args, cfg, state):
    """A moment the live watcher signaled: cut it from the stream's live VOD and post it."""
    from moments import Moment
    s = next((x for x in enabled_streamers(cfg) if x["login"].lower() == args.live.lower()), None)
    if not s:
        sys.exit(f"No enabled streamer with login {args.live} in config.yaml")
    lcfg = cfg.get("live") or {}
    post = not args.dry_run and lcfg.get("post", False)
    if post and s["name"] in done_today(state, cfg):
        print(f"{s['name']} already has a Short today. Nothing to do.")
        return
    if post and posted_today(state) >= cfg.get("max_uploads_per_day", 6):
        print("Already posted today's maximum (YouTube's daily upload quota). Nothing to do.")
        return
    vod_id = args.vod or (twitch.live_vod(s["login"]) or {}).get("id")
    if not vod_id:
        sys.exit(f"{s['name']} has no VOD for this stream (VODs off or subscriber-only), so it can't be clipped.")
    t = float(args.t)
    # the live VOD trails the stream a little: wait until it has the whole moment
    need = t + context(cfg)[1]
    for _ in range(40):
        vod = next((v for v in twitch.latest_vods(s["login"], n=3) if v["id"] == vod_id), None)
        if vod and vod["duration"] >= need:
            break
        print(f"  waiting for the VOD to reach {need:.0f}s (now {vod['duration'] if vod else '?'}s)")
        time.sleep(15)
    m = Moment(source="live", vod={"id": vod_id, "duration": None}, t=t, live=True,
               created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    print(f"Live moment from the watcher: {s['name']}, {args.rate or '?'} chats/min per 1,000 viewers")
    try:
        run_one(s, m, cfg, state, dry_run=not post)
    except safety.Unsafe as e:
        print(f"Not posting this moment: {e}. The watcher will signal the next one.")
        return
    if not post:
        print("Not uploaded (dry run, or live.post is off in config.yaml). The video is in this run's Artifacts.")
    else:
        st.save(state)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clips", type=int, help="how many Shorts (default: clips_per_run in config.yaml)")
    ap.add_argument("--streamer", help="Twitch login, or 'all' (default)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--slot", help="posting time (ISO, from slots.py): make one Short and post it then")
    ap.add_argument("--next", action="store_true", help=argparse.SUPPRESS)   # old name, same as default
    ap.add_argument("--live", help="Twitch login: clip a live moment the watcher signaled")
    ap.add_argument("--vod", help="with --live: the live VOD's id")
    ap.add_argument("--t", help="with --live: seconds into the VOD")
    ap.add_argument("--rate", help=argparse.SUPPRESS)
    args = ap.parse_args()

    with open(os.path.join(HERE, "config.yaml")) as f:
        cfg = yaml.safe_load(f)
    streamers = enabled_streamers(cfg)
    state = st.load()
    if args.live:
        return clip_live(args, cfg, state)
    if args.streamer and args.streamer != "all":
        streamers = [s for s in streamers if s["login"] == args.streamer]
        if not streamers:
            sys.exit(f"No enabled streamer with login {args.streamer} in config.yaml")
    elif not args.dry_run:
        # one Short per streamer per day: whoever already got one (live or scheduled) is done
        done = done_today(state, cfg)
        if done:
            print("Already posted today: " + ", ".join(sorted(done)))
        streamers = [s for s in streamers if s["name"] not in done]
        if not streamers:
            print("Every streamer already has a Short today. Nothing to do.")
            return
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
    try:
        import metadata
        metadata.refresh_templates(cfg, state)    # at most once a week, see ai.templates_every_days
    except Exception as e:
        print(f"(couldn't refresh the title templates: {e})")
    # streamers posted longest ago go first, so everyone gets turns
    # Streamers who are live right now go last: the live watcher may still
    # catch a viral moment of theirs today, which beats a scheduled pick.
    try:
        live_now = set(twitch.live_streams([s["login"] for s in streamers]))
    except Exception:
        live_now = set()
    if live_now:
        print("Live right now (left for the live watcher if possible): "
              + ", ".join(s["name"] for s in streamers if s["login"].lower() in live_now))
    order = sorted(streamers, key=lambda s: (s["login"].lower() in live_now, last_posted(state, s)))
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
