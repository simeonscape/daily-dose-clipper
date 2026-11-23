"""Keep an eye on the streamers' live streams and post their most viral moment.

    python live.py                          # watch (config.yaml `live:`), post when it fires
    python live.py --minutes 60 --dry-run   # watch an hour, render but don't upload
    python live.py --streamer kaicenat

How it works:
  - Every minute it checks which of the streamers are live, and watches each
    one: chat (read-only, anonymous) and the last few minutes of video.
  - Chat speed is measured per 1,000 viewers: messages a minute divided by
    viewers / 1000, over the last 20 seconds. When it goes over
    `live.chat_per_1000` (and is clearly above that stream's own normal),
    that's a viral moment. Once chat slows down again, the moment is cut,
    blurred where needed, edited and posted right away.
  - One Short per streamer per day (posting time zone). A streamer who already
    has one today, live or from the schedule, is checked off and not watched;
    the scheduled runs skip them too.
  - Every 10 minutes it logs each stream's normal and peak chat speed per
    1,000 viewers, so the threshold can be tuned from real numbers.
  - When nobody is live it keeps checking, so the workflow is a constant eye.
"""
import argparse
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timezone

import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "engine"))
import live, pipeline, safety, spikes, twitch  # noqa: E402
import state as st  # noqa: E402
from moments import Moment  # noqa: E402
from slots import done_today, enabled_streamers, posted_today, quota_day  # noqa: E402

WORK = os.path.join(HERE, "work")
OUT = os.path.join(HERE, "output")
PAD = 15

DEFAULTS = {"enabled": True, "post": False, "max_minutes": 320, "max_streams": 5, "buffer_s": 300,
            "chat_per_1000": 100, "min_chat_z": 3.0, "rate_window_s": 20, "quality": "1080p60,1080p,best"}


class Watch:
    def __init__(self, s, stream, lcfg):
        self.s, self.stream = s, stream
        self.viewers = stream.get("viewer_count", 0)
        self.chat = live.ChatListener(s["login"])
        self.rec = live.Recorder(s["login"], WORK, buffer_s=lcfg["buffer_s"], quality=lcfg["quality"])
        self.trig = live.RateTrigger(threshold=float(lcfg["chat_per_1000"]), window_s=float(lcfg["rate_window_s"]),
                                     min_z=float(lcfg["min_chat_z"]))
        self.started = datetime.fromisoformat(stream["started_at"].replace("Z", "+00:00")).timestamp()
        self.busy = False            # a Short of this stream is being made right now
        try:
            self.vod = twitch.live_vod(s["login"], stream)
        except Exception:
            self.vod = None

    def start(self):
        self.chat.start()
        self.rec.start()
        print(f"Watching {self.s['name']} live: \"{self.stream.get('title', '')[:60]}\" "
              f"({self.viewers:,} viewers" + (f", VOD {self.vod['id']} recording)" if self.vod else ", no VOD)"))

    def stop(self):
        self.chat.stop.set()
        self.rec.stop()
        shutil.rmtree(self.rec.dir, ignore_errors=True)


def capture(w, spike, cfg):
    """Cut the moment out of the buffer. Returns a Moment with .raw (video) set."""
    delay, before, after = cfg.get("chat_delay_s", 6), cfg["before_s"], cfg["after_s"]
    centre = spike["t"] - delay
    a, b = centre - before - PAD, centre + after + PAD
    if not live.wait_for(w.rec, b, timeout=90):
        print(f"  [{w.s['login']}] the recording didn't catch up, using what we have")
    os.makedirs(WORK, exist_ok=True)
    raw = os.path.join(WORK, f"live_{w.s['login']}_{int(centre)}.mp4")
    real_a = w.rec.cut(a, b, raw)
    feats = spikes.chat_features(w.chat.window(a - 200, b + 30), spike["t"])
    m = Moment(source="live", vod=w.vod, t=round(centre - w.started, 2), live=True, chat=feats,
               created_at=datetime.fromtimestamp(centre, timezone.utc).isoformat(),
               messages=[t for _, t in w.chat.window(centre - 5, centre + after + 8)][:80],
               key_override=None if w.vod else f"live:{w.s['login']}:{w.stream['id']}")
    m.raw, m.centre_in_raw = raw, centre - real_a
    m.chat_rel = [(t - real_a, body) for t, body in w.chat.window(real_a, b + 15)]
    m.rate = spike["rate"]
    return m


def in_ci():
    return os.environ.get("GITHUB_ACTIONS") == "true"


def git(*args, check=True):
    return subprocess.run(["git", *args], cwd=HERE, capture_output=True, text=True, check=check)


def pull_state(state, lock):
    """Merge in what the scheduled runs posted meanwhile (from GitHub)."""
    if not in_ci():
        return
    try:
        git("fetch", "-q", "origin", "main")
        import json
        theirs = json.loads(git("show", "origin/main:state.json").stdout)
        with lock:
            merged = st.merge(state, theirs)
            state.clear(); state.update(merged)
    except Exception as e:
        print(f"(couldn't refresh state from GitHub: {e})")


def push_state(state, lock):
    """Save state.json to GitHub right away, so the scheduled runs see the post."""
    with lock:
        st.save(state)
    if not in_ci():
        return
    import json
    mine = json.loads(json.dumps(state))
    for i in range(4):
        try:
            git("fetch", "-q", "origin", "main")
            git("reset", "-q", "--hard", "origin/main")
            theirs = st.load()
            st.save(st.merge(mine, theirs))
            git("add", "state.json")
            if git("diff", "--cached", "--quiet", check=False).returncode == 0:
                return
            git("-c", "user.name=clipper-bot", "-c", "user.email=clipper-bot@users.noreply.github.com",
                "commit", "-qm", "Update posted clips (live)")
            git("push", "-q", "origin", "HEAD:main")
            return
        except subprocess.CalledProcessError as e:
            print(f"(saving state failed, try {i + 1}: {e.stderr.strip()[:200]})")
            time.sleep(5 * (i + 1))


def process(w, m, cfg, lcfg, state, lock, dry_run):
    """Edit and post one live moment. Returns True if the streamer is now done for today."""
    s = w.s
    print(f"\n== LIVE {s['name']}: {m.rate:.0f} chats/min per 1,000 viewers ({w.viewers:,} watching), "
          + m.describe())
    tag = f"{s['login']}_live_{int(time.time())}"
    seg = os.path.join(WORK, f"seg_{tag}.mp4")
    os.replace(m.raw, seg)
    cut, _ = pipeline.best_window(seg, m.centre_in_raw, cfg, chat=m.chat_rel, seg_start=0.0)
    out, meta = pipeline.make_short(s, cut, m.messages, cfg, tag, OUT, WORK)
    if dry_run or not lcfg["post"]:
        print("  not uploading (dry run, or live.post is off in config.yaml)")
        return True               # in a test, one per streamer is enough too
    pull_state(state, lock)
    with lock:
        if s["name"] in done_today(state, cfg):
            print(f"  {s['name']} already got a Short today meanwhile, not uploading")
            return True
        if posted_today(state) >= cfg.get("max_uploads_per_day", 6):
            print("  YouTube's daily upload limit is used up, not uploading")
            return False
    import youtube
    video_id = youtube.upload(out, meta, privacy=cfg.get("privacy", "public"))
    print(f"  uploaded: https://youtube.com/shorts/{video_id}")
    with lock:
        st.mark_posted(state, s["name"], m, video_id, meta, quota_day=quota_day(),
                       extra={"layout": meta.get("layout"), "live": True, "chat_per_1000": round(m.rate, 1),
                              "viewers": w.viewers})
    push_state(state, lock)
    return True


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
    streamers = enabled_streamers(cfg)
    if args.streamer and args.streamer != "all":
        streamers = [s for s in streamers if s["login"] == args.streamer]
    state = st.load()
    lock = threading.Lock()
    tested = set()                   # dry run: one Short per streamer per watch
    print(f"Watching for chat over {lcfg['chat_per_1000']} messages/min per 1,000 viewers "
          f"({'posting' if lcfg['post'] and not args.dry_run else 'not posting'}).")

    jobs = queue.Queue()
    watches = {}

    def worker():
        while True:
            item = jobs.get()
            if item is None:
                return
            w, spike = item
            try:
                m = capture(w, spike, cfg)
                if process(w, m, cfg, lcfg, state, lock, args.dry_run):
                    tested.add(w.s["name"])
            except safety.Unsafe as e:
                print(f"  skipped: {e}. Waiting for the next moment.")
            except Exception as e:
                print(f"  failed: {e}")
                traceback.print_exc()
            finally:
                w.busy = False

    th = threading.Thread(target=worker, daemon=True)
    th.start()

    deadline = time.time() + 60 * (args.minutes or lcfg["max_minutes"])
    next_check = next_log = next_pull = 0.0
    try:
        while time.time() < deadline:
            now = time.time()
            if now >= next_pull:
                next_pull = now + 300
                pull_state(state, lock)
            if now >= next_check:
                next_check = now + 60
                with lock:
                    done = (set() if args.dry_run else done_today(state, cfg)) | tested
                try:
                    streams = twitch.live_streams([s["login"] for s in streamers])
                except Exception as e:
                    print(f"(couldn't check who's live: {e})")
                    streams = None
                if streams is not None:
                    for login, w in list(watches.items()):
                        if login not in streams or (w.s["name"] in done and not w.busy):
                            why = "is done for today" if w.s["name"] in done else "went offline"
                            print(f"Stopped watching {w.s['name']}: {why}")
                            w.stop()
                            del watches[login]
                        else:
                            w.viewers = streams[login].get("viewer_count", w.viewers)
                    want = [s for s in streamers if s["login"].lower() in streams and s["name"] not in done
                            and s["login"].lower() not in watches]
                    want.sort(key=lambda s: -streams[s["login"].lower()].get("viewer_count", 0))
                    for s in want[: max(0, lcfg["max_streams"] - len(watches))]:
                        w = Watch(s, streams[s["login"].lower()], lcfg)
                        w.start()
                        watches[s["login"].lower()] = w
                    if not watches and now >= next_log:
                        print(f"{datetime.now(timezone.utc):%H:%M} UTC: nobody to watch "
                              f"(live: {', '.join(streams) or 'nobody'}; done today: {', '.join(sorted(done)) or 'nobody'})")
            if now >= next_log:
                next_log = now + 600
                for w in watches.values():
                    print(f"{datetime.now(timezone.utc):%H:%M} UTC {w.s['name']}: {w.viewers:,} viewers, chat "
                          f"{w.trig.typical():.0f}/min per 1,000 normally, peak {w.trig.peak_rate:.0f}"
                          + ("" if w.chat.connected else " (chat not connected)"))
            for w in list(watches.values()):
                if not w.rec.alive():
                    continue
                w.rec.prune()
                spike = w.trig.update(w.chat.window(now - 320, now), now, w.viewers)
                if spike and not w.busy:
                    print(f"[{w.s['login']}] chat at {spike['rate']:.0f}/min per 1,000 viewers "
                          f"(z {spike['z']:.1f}): clipping it")
                    w.busy = True
                    jobs.put((w, spike))
            time.sleep(1)
    finally:
        jobs.put(None)
        th.join()
        for w in watches.values():
            w.stop()
    print("Live watch finished.")


if __name__ == "__main__":
    main()
