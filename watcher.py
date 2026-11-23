"""The always-on watcher: reads the streamers' live chats and signals the
clipper the moment one explodes.

    python watcher.py              # run (on GitHub this is the "Live watcher" workflow)
    python watcher.py --dry-run    # only print the signals

It never stops on its own (GitHub ends a run after ~6 hours; the workflow
starts the next one straight away). What it does, all the time:
  - Every minute: which of the streamers are live, and how many viewers.
  - Reads every live streamer's chat (read-only, anonymous).
  - Chat speed per 1,000 viewers = messages a minute / (viewers / 1000), over
    the last 20 s. Over `live.chat_per_1000`, and clearly above that stream's
    own normal: a viral moment. It sends a signal to the "Clip now" workflow
    (streamer, which VOD, the second it happened), which cuts the moment
    from the stream's live VOD, edits it and posts it.
  - One Short per streamer per day: a streamer who already has one (live or
    scheduled) is checked off and gets no more signals that day.
  - Every 10 minutes it logs each stream's normal and peak chat speed per
    1,000 viewers, so the threshold can be tuned from real numbers.
Light on purpose: no video here, only chat, so it can run around the clock.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

import requests
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "engine"))
import live, twitch  # noqa: E402
import state as st  # noqa: E402
from slots import done_today, enabled_streamers, local_day  # noqa: E402

DEFAULTS = {"enabled": True, "chat_per_1000": 100, "min_chat_z": 3.0, "rate_window_s": 20,
            "run_hours": 5.75, "retry_after_min": 30}


def load_cfg():
    with open(os.path.join(HERE, "config.yaml")) as f:
        cfg = yaml.safe_load(f)
    return cfg, dict(DEFAULTS, **(cfg.get("live") or {}))


def fresh_state():
    """state.json as it is on GitHub now (the clipper runs save there)."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        try:
            subprocess.run(["git", "fetch", "-q", "origin", "main"], cwd=HERE, check=True, capture_output=True)
            out = subprocess.run(["git", "show", "origin/main:state.json"], cwd=HERE, check=True,
                                 capture_output=True, text=True).stdout
            s = json.loads(out)
            s.setdefault("posted", [])
            return s
        except Exception as e:
            print(f"(couldn't read state from GitHub: {e})")
    return st.load()


def signal(s, stream, vod, t, rate, z, viewers, dry_run):
    """Start the "Clip now" workflow for this moment."""
    inputs = {"streamer": s["login"], "vod": vod["id"] if vod else "", "t": f"{t:.0f}",
              "rate": f"{rate:.0f}", "viewers": str(viewers), "chat_z": f"{z:.1f}"}
    print(f">>> SIGNAL {s['name']}: {rate:.0f} chats/min per 1,000 viewers ({viewers:,} watching, z {z:.1f}) "
          f"at {t:.0f}s into the stream" + (f", VOD {vod['id']}" if vod else ", no VOD"))
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if dry_run or not (token and repo):
        print("    (not sent: dry run or not on GitHub)")
        return True
    r = requests.post(f"https://api.github.com/repos/{repo}/actions/workflows/clip-now.yml/dispatches",
                      headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
                      json={"ref": "main", "inputs": inputs}, timeout=30)
    if r.status_code >= 300:
        print(f"    couldn't start Clip now: {r.status_code} {r.text[:200]}")
        return False
    print("    Clip now started")
    return True


class Watch:
    def __init__(self, s, stream, lcfg):
        self.s, self.stream = s, stream
        self.viewers = stream.get("viewer_count", 0)
        self.started = datetime.fromisoformat(stream["started_at"].replace("Z", "+00:00")).timestamp()
        self.chat = live.ChatListener(s["login"], keep_s=400)
        self.trig = live.RateTrigger(threshold=float(lcfg["chat_per_1000"]), window_s=float(lcfg["rate_window_s"]),
                                     min_z=float(lcfg["min_chat_z"]))
        self.vod = None
        self.chat.start()

    def find_vod(self):
        if self.vod is None:
            try:
                self.vod = twitch.live_vod(self.s["login"], self.stream) or False
            except Exception:
                self.vod = None
        return self.vod or None

    def stop(self):
        self.chat.stop.set()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="print signals, don't start the clipper")
    ap.add_argument("--hours", type=float, help=argparse.SUPPRESS)
    args = ap.parse_args()
    cfg, lcfg = load_cfg()
    if not lcfg["enabled"]:
        print("The live watcher is off (live.enabled in config.yaml).")
        return
    streamers = enabled_streamers(cfg)
    delay = cfg.get("chat_delay_s", 6)
    print(f"Watching {', '.join(s['name'] for s in streamers)} for chat over "
          f"{lcfg['chat_per_1000']}/min per 1,000 viewers.")

    watches = {}
    signaled = {}                     # streamer -> (day, time of last signal)
    state, next_state = None, 0.0
    next_check = next_log = 0.0
    end = time.time() + 3600 * (args.hours or lcfg["run_hours"])
    try:
        while time.time() < end:
            now = time.time()
            if now >= next_state:
                state, next_state = fresh_state(), now + 300
            day = local_day(cfg)
            done = done_today(state, cfg)
            if now >= next_check:
                next_check = now + 60
                try:
                    streams = twitch.live_streams([s["login"] for s in streamers])
                except Exception as e:
                    print(f"(couldn't check who's live: {e})")
                    streams = None
                if streams is not None:
                    for login, w in list(watches.items()):
                        if login not in streams or w.stream["id"] != streams[login]["id"]:
                            print(f"{w.s['name']} went offline")
                            w.stop()
                            del watches[login]
                        else:
                            w.viewers = streams[login].get("viewer_count", w.viewers)
                    for s in streamers:
                        login = s["login"].lower()
                        if login in streams and login not in watches:
                            watches[login] = Watch(s, streams[login], lcfg)
                            print(f"{s['name']} is live: \"{streams[login].get('title', '')[:60]}\" "
                                  f"({watches[login].viewers:,} viewers)"
                                  + (" (already has a Short today)" if s["name"] in done else ""))
            if now >= next_log:
                next_log = now + 600
                stamp = f"{datetime.now(timezone.utc):%H:%M} UTC"
                if not watches:
                    print(f"{stamp}: nobody live. Done today: {', '.join(sorted(done)) or 'nobody'}")
                for w in watches.values():
                    print(f"{stamp} {w.s['name']}: {w.viewers:,} viewers, chat {w.trig.typical():.0f}/min "
                          f"per 1,000 normally, peak {w.trig.peak_rate:.0f}"
                          + (", done today" if w.s["name"] in done else "")
                          + ("" if w.chat.connected else " (chat reconnecting)"))
            for w in watches.values():
                hit = w.trig.update(w.chat.window(now - 320, now), now, w.viewers)
                if not hit:
                    continue
                name = w.s["name"]
                last = signaled.get(name)
                if name in done:
                    print(f"[{name}] chat at {hit['rate']:.0f}/min per 1,000, but {name} already has a Short today")
                    continue
                if last and last[0] == day and now - last[1] < 60 * lcfg["retry_after_min"]:
                    print(f"[{name}] chat at {hit['rate']:.0f}/min per 1,000, a Short is already being made")
                    continue
                t = hit["t"] - delay - w.started
                if signal(w.s, w.stream, w.find_vod(), t, hit["rate"], hit["z"], w.viewers, args.dry_run):
                    signaled[name] = (day, now)
            time.sleep(1)
    finally:
        for w in watches.values():
            w.stop()
    print("Watcher run finished; the next run takes over.")


if __name__ == "__main__":
    main()
