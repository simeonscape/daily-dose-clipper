"""The always-on watcher: reads the streamers' live chats and signals the
clipper the moment one explodes.

    python watcher.py              # run (on GitHub this is the "Live watcher" workflow)
    python watcher.py --dry-run    # only print the signals

It never stops: GitHub ends a run after ~6 hours, so near the end each run
starts its own replacement and keeps watching until the new one is warmed up
(handover), so there's no gap. A run every 15 minutes is only a safety net:
it quits at once when a watcher is already running.
What it does, all the time:
  - Every minute: which of the streamers are live, and how many viewers.
  - Reads every live streamer's chat as it comes in (read-only, anonymous).
  - Every second: chat speed per 1,000 viewers = messages a minute /
    (viewers / 1000), over the last 20 s. Over `live.chat_per_1000`, clearly above that stream's
    own normal, AND chat is reacting (enough LOL / CLIP IT / OMG / DAMN-type
    messages, hellos don't count; the first `live.skip_start_min` minutes of
    a stream are skipped): a viral moment. It sends a signal to the "Clip now" workflow
    (streamer, which VOD, the second it happened), which cuts the moment
    from the stream's live VOD, edits it and posts it.
  - One Short per streamer per day: a streamer who already has one (live or
    scheduled) is checked off and gets no more signals that day.
  - Every minute (while someone is live) it logs each stream's chat speed
    now, normal and peak per 1,000 viewers, so the threshold can be tuned
    from real numbers. These log lines are only a summary: chat is checked
    every second.
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
            "min_reaction_share": 0.3, "min_reactions": 8, "skip_start_min": 5,
            "run_hours": 5.75, "retry_after_min": 30}
HANDOVER_WARM_S = 150      # a new run is warmed up (knows each chat's normal) after this long
HANDOVER_MAX_S = 8 * 60    # the old run stops by then, warmed up or not


def gh(method, path, **kw):
    """GitHub API call for this repo, or None when not on GitHub / it failed."""
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not (token and repo):
        return None
    try:
        r = requests.request(method, f"https://api.github.com/repos/{repo}/{path}", timeout=30,
                             headers={"Authorization": f"Bearer {token}",
                                      "Accept": "application/vnd.github+json"}, **kw)
    except requests.RequestException as e:
        print(f"(GitHub API: {e})")
        return None
    if r.status_code >= 300:
        print(f"(GitHub API {path}: {r.status_code} {r.text[:200]})")
        return None
    return r.json() if r.content else {}


def other_watchers():
    """Other running Live watcher runs: [(run id, seconds since it started)].
    None if it couldn't be checked."""
    data = gh("GET", "actions/workflows/watcher.yml/runs", params={"status": "in_progress", "per_page": 20})
    if data is None:
        return None
    me, now, out = str(os.environ.get("GITHUB_RUN_ID", "")), time.time(), []
    for run in data.get("workflow_runs", []):
        if str(run["id"]) == me:
            continue
        started = datetime.fromisoformat((run.get("run_started_at") or run["created_at"]).replace("Z", "+00:00"))
        out.append((run["id"], now - started.timestamp()))
    return out


def start_replacement():
    """Start the next watcher run (it takes over when it's warmed up)."""
    ok = gh("POST", "actions/workflows/watcher.yml/dispatches",
            json={"ref": "main", "inputs": {"handover": "true"}}) is not None
    print("Started the next watcher run; handing over once it's warmed up" if ok
          else "Couldn't start the next watcher run; the 15-minute safety net will")
    return ok


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
                                     min_z=float(lcfg["min_chat_z"]),
                                     min_reaction_share=float(lcfg["min_reaction_share"]),
                                     min_reactions=int(lcfg["min_reactions"]))
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
    ap.add_argument("--handover", action="store_true",
                    help="started by the previous run: watch, but stay quiet until it has stopped")
    args = ap.parse_args()
    cfg, lcfg = load_cfg()
    if not lcfg["enabled"]:
        print("The live watcher is off (live.enabled in config.yaml).")
        return
    on_github = os.environ.get("GITHUB_ACTIONS") == "true"
    if on_github and not args.handover:
        others = other_watchers()
        if others:
            print(f"A watcher is already running (run {others[0][0]}), so this one isn't needed.")
            return
    streamers = enabled_streamers(cfg)
    delay = cfg.get("chat_delay_s", 6)
    print(f"Watching {', '.join(s['name'] for s in streamers)} for chat over "
          f"{lcfg['chat_per_1000']}/min per 1,000 viewers with chat reacting (LOL, CLIP IT, OMG...), "
          f"checked every second.")

    started_at = time.time()
    watches = {}
    signaled = {}                     # streamer -> (day, time of last signal)
    state, next_state = None, 0.0
    next_check = next_log = 0.0
    end = time.time() + 3600 * (args.hours or lcfg["run_hours"])
    hard_end = end + HANDOVER_MAX_S
    # handover: the new run watches (to learn each chat's normal) but only
    # sends signals once the previous run has stopped, so nothing is sent twice
    sending, quiet_until = not (args.handover and on_github), time.time() + HANDOVER_MAX_S + 120
    replacement = False            # next run started (old run)
    next_peer = 0.0
    if not sending:
        print("Taking over from the previous run: warming up, it keeps watching meanwhile.")
    try:
        while time.time() < hard_end:
            now = time.time()
            if now >= next_peer and (not sending or (on_github and now >= end)):
                next_peer = now + 10
                if not sending:
                    others = other_watchers()
                    older = [o for o in (others or []) if o[1] > now - started_at]
                    if (others is not None and not older) or now >= quiet_until:
                        sending = True
                        print("Previous run has stopped: this run is now the one sending signals.")
                else:
                    if not replacement:
                        replacement = start_replacement()
                        if not replacement:
                            end = hard_end      # keep going to the limit; the cron safety net follows
                    else:
                        others = other_watchers() or []
                        newer = [o for o in others if o[1] < now - started_at]
                        calm = not any(w.trig.open for w in watches.values())
                        if any(age >= HANDOVER_WARM_S for _, age in newer) and calm:
                            print("The next run is warmed up; handing over.")
                            break
            elif not on_github and now >= end:
                break
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
                next_log = now + (60 if watches else 600)
                stamp = f"{datetime.now(timezone.utc):%H:%M} UTC"
                if not watches:
                    print(f"{stamp}: nobody live. Done today: {', '.join(sorted(done)) or 'nobody'}")
                for w in watches.values():
                    now_rate = w.trig.rates[-1] if w.trig.rates else 0.0
                    print(f"{stamp} {w.s['name']}: {w.viewers:,} viewers, chat {now_rate:.0f}/min per 1,000 now, "
                          f"{w.trig.typical():.0f} normally, peak {w.trig.peak_rate:.0f}"
                          + (", done today" if w.s["name"] in done else "")
                          + ("" if w.chat.connected else " (chat reconnecting)"))
            for w in watches.values():
                if w.viewers <= 0:          # Twitch shows 0 for the first minutes; per-1,000 means nothing yet
                    continue
                hit = w.trig.update(w.chat.window(now - 320, now), now, w.viewers)
                if not hit:
                    continue
                name = w.s["name"]
                react = f"{hit['reactions']} reactions ({hit['reaction_share']:.0%} of chat)"
                if hit.get("skipped"):
                    print(f"[{name}] chat at {hit['rate']:.0f}/min per 1,000, but only {react}: not a moment")
                    continue
                if hit["t"] - w.started < 60 * float(lcfg["skip_start_min"]):
                    print(f"[{name}] chat at {hit['rate']:.0f}/min per 1,000 with {react}, "
                          f"but the stream only just started: not a moment")
                    continue
                if not sending:
                    print(f"[{name}] chat at {hit['rate']:.0f}/min per 1,000 (the previous run handles it)")
                    continue
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
