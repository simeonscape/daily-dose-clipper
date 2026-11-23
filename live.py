"""Clip streams while they're live.

    python live.py                          # watch whoever is live now (config.yaml `live:`)
    python live.py --minutes 60 --dry-run   # watch an hour, render but don't upload
    python live.py --streamer kaicenat

For each configured streamer who is live (the biggest streams first, up to
`live.max_streams`), it reads chat, keeps the last few minutes of video, and
whenever chat explodes it cuts that moment. Every `pick_every_min` minutes the
best moment so far (chat explosion, laughter, CLIP IT spam, plus Twitch clips
viewers already made of it) becomes a Short, if it's strong enough and the day
still has room (`live.max_posts_per_day`, and YouTube's daily upload limit
shared with the scheduled Shorts).

The scheduled Shorts (run.py) already use clips and chat from a stream that is
still live. This is for moments that are only seconds old.
"""
import argparse
import os
import queue
import sys
import threading
import time
import traceback
from datetime import datetime, timezone

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "engine"))
import live, pipeline, safety, spikes, twitch, virality  # noqa: E402
import state as st  # noqa: E402
from moments import Moment  # noqa: E402
from slots import posted_today, quota_day  # noqa: E402

WORK = os.path.join(HERE, "work")
OUT = os.path.join(HERE, "output")
PAD = 15

DEFAULTS = {"enabled": True, "post": False, "max_minutes": 170, "max_streams": 2, "buffer_s": 300,
            "min_chat_z": 6.0, "pick_every_min": 30, "max_posts_per_day": 1, "quality": "1080p60,1080p,best"}


def live_posts_today(state):
    today = quota_day()
    return sum(1 for p in state["posted"] if p.get("video_id") and p.get("source") == "live"
               and p.get("quota_day") == today)


class Watch:
    def __init__(self, s, stream, lcfg):
        self.s, self.stream = s, stream
        self.chat = live.ChatListener(s["login"])
        self.rec = live.Recorder(s["login"], WORK, buffer_s=lcfg["buffer_s"], quality=lcfg["quality"])
        self.det = live.Detector(min_score=lcfg["min_chat_z"] * 0.66)
        self.started = datetime.fromisoformat(stream["started_at"].replace("Z", "+00:00")).timestamp()
        try:
            self.vod = twitch.live_vod(s["login"], stream)
        except Exception:
            self.vod = None

    def start(self):
        self.chat.start()
        self.rec.start()
        print(f"Watching {self.s['name']} live: \"{self.stream.get('title', '')[:60]}\" "
              f"({self.stream.get('viewer_count', 0):,} viewers"
              + (f", VOD {self.vod['id']} recording)" if self.vod else ", no VOD)"))

    def stop(self):
        self.chat.stop.set()
        self.rec.stop()


def capture(w, spike, cfg, lcfg):
    """Cut the moment out of the buffer and rate it. Returns a Moment with .raw (video) set."""
    delay, before, after = cfg.get("chat_delay_s", 6), cfg["before_s"], cfg["after_s"]
    centre = spike["t"] - delay
    a, b = centre - before - PAD, centre + after + PAD
    if not live.wait_for(w.rec, b, timeout=90):
        print(f"  [{w.s['login']}] the recording didn't catch up, using what we have")
    os.makedirs(WORK, exist_ok=True)
    raw = os.path.join(WORK, f"live_{w.s['login']}_{int(centre)}.mp4")
    real_a = w.rec.cut(a, b, raw)
    chat_abs = w.chat.window(a - 200, b + 30)
    feats = spikes.chat_features(chat_abs, spike["t"])
    offset = centre - w.started           # seconds into the stream (and its VOD)
    m = Moment(source="live", vod=w.vod, t=round(offset, 2), live=True, chat=feats,
               created_at=datetime.fromtimestamp(centre, timezone.utc).isoformat(),
               messages=[t for _, t in w.chat.window(centre - 5, centre + after + 8)][:80],
               key_override=None if w.vod else f"live:{w.s['login']}:{w.stream['id']}")
    m.raw, m.raw_start, m.centre_in_raw = raw, real_a, centre - real_a
    m.chat_rel = [(t - real_a, body) for t, body in w.chat.window(real_a, b + 15)]
    m.watch = w
    return m


def add_live_clips(m):
    """Viewers may have clipped it already: add those clip views to the score."""
    if not m.vod:
        return
    try:
        for c in twitch.top_clips(m.watch.s["login"], days=1):
            if c.get("video_id") == m.vod["id"] and c.get("vod_offset") is not None:
                if abs(c["vod_offset"] + 0.7 * c["duration"] - m.t) < 40:
                    m.views += c["view_count"]; m.clips += 1; m.clip_ids.append(c["id"])
        if m.clip_ids:
            m.clips -= 1
    except Exception as e:
        print(f"  (couldn't read live clips: {e})")


def process(m, cfg, lcfg, state, lock, dry_run):
    s = m.watch.s
    print(f"\n== LIVE {s['name']}: {m.describe()}")
    tag = f"{s['login']}_live_{int(time.time())}"
    seg = os.path.join(WORK, f"seg_{tag}.mp4")
    os.replace(m.raw, seg)
    cut, _ = pipeline.best_window(seg, m.centre_in_raw, cfg, chat=m.chat_rel, seg_start=0.0)
    out, meta = pipeline.make_short(s, cut, m.messages, cfg, tag, OUT, WORK)
    if dry_run or not lcfg["post"]:
        print("  not uploading (dry run or live.post is off)")
        return
    with lock:
        if posted_today(state) >= cfg.get("max_uploads_per_day", 6) or live_posts_today(state) >= lcfg["max_posts_per_day"]:
            print("  no room left today, not uploading")
            return
    import youtube
    video_id = youtube.upload(out, meta, privacy=cfg.get("privacy", "public"))
    print(f"  uploaded: https://youtube.com/shorts/{video_id}")
    with lock:
        st.mark_posted(state, s["name"], m, video_id, meta, quota_day=quota_day(),
                       extra={"layout": meta.get("layout"), "live": True})
        st.save(state)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, help="how long to watch (default live.max_minutes)")
    ap.add_argument("--streamer", help="Twitch login, or 'all'")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    with open(os.path.join(HERE, "config.yaml")) as f:
        cfg = yaml.safe_load(f)
    lcfg = dict(DEFAULTS, **(cfg.get("live") or {}))
    if not lcfg["enabled"]:
        print("Live clipping is off (live.enabled in config.yaml).")
        return
    streamers = [s for s in cfg["streamers"] if s.get("platform", "twitch") == "twitch" and s.get("enabled", True)]
    if args.streamer and args.streamer != "all":
        streamers = [s for s in streamers if s["login"] == args.streamer]
    state = st.load()
    if not args.dry_run and lcfg["post"] and live_posts_today(state) >= lcfg["max_posts_per_day"]:
        print("Today's live Short is already posted.")
        return

    streams = twitch.live_streams([s["login"] for s in streamers])
    live_now = sorted([s for s in streamers if s["login"].lower() in streams],
                      key=lambda s: -streams[s["login"].lower()].get("viewer_count", 0))[: lcfg["max_streams"]]
    if not live_now:
        print("Nobody is live right now.")
        return

    watches = [Watch(s, streams[s["login"].lower()], lcfg) for s in live_now]
    for w in watches:
        w.start()

    lock = threading.Lock()
    jobs = queue.Queue()

    def worker():
        while True:
            m = jobs.get()
            if m is None:
                return
            try:
                process(m, cfg, lcfg, state, lock, args.dry_run)
            except safety.Unsafe as e:
                print(f"  skipped: {e}")
            except Exception as e:
                print(f"  failed: {e}")
                traceback.print_exc()

    th = threading.Thread(target=worker, daemon=True)
    th.start()

    deadline = time.time() + 60 * (args.minutes or lcfg["max_minutes"])
    next_pick = time.time() + 60 * lcfg["pick_every_min"]
    pending, found = [], []
    try:
        while time.time() < deadline and any(w.rec.alive() for w in watches):
            now = time.time()
            for w in watches:
                if not w.rec.alive():
                    continue
                w.rec.prune()
                spike = w.det.update(w.chat.window(now - w.det.baseline_s - 10, now), now)
                if spike:
                    print(f"[{w.s['login']}] chat spike, z {spike['z']:.1f}")
                    pending.append((spike["t"] + cfg["after_s"] + PAD + 8, w, spike))
            for item in [p for p in pending if p[0] <= now]:
                pending.remove(item)
                _, w, spike = item
                try:
                    m = capture(w, spike, cfg, lcfg)
                    found.append(m)
                    print(f"  captured {w.s['name']} moment: {m.describe()}")
                except Exception as e:
                    print(f"  couldn't capture: {e}")
            if now >= next_pick and found:
                next_pick = now + 60 * lcfg["pick_every_min"]
                for m in found:
                    add_live_clips(m)
                best = virality.rank(found, cfg)[0]
                if best.chat.get("z", 0) >= lcfg["min_chat_z"] or best.views >= cfg.get("min_clip_views", 200):
                    jobs.put(best)
                    found.remove(best)
                else:
                    print(f"Best moment so far isn't strong enough yet ({best.describe()})")
                for m in found[:-5]:          # keep only the latest few as fallbacks
                    try:
                        os.remove(m.raw)
                    except OSError:
                        pass
                found = found[-5:]
            time.sleep(1)
    finally:
        for w in watches:
            w.stop()
    if found:
        for m in found:
            add_live_clips(m)
        best = virality.rank(found, cfg)[0]
        if best.chat.get("z", 0) >= lcfg["min_chat_z"] or best.views >= cfg.get("min_clip_views", 200):
            jobs.put(best)
    jobs.put(None)
    th.join()
    print("Live watch finished.")


if __name__ == "__main__":
    main()
