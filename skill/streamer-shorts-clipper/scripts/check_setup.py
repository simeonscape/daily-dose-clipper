"""Check a configured clipper repo before the first run.

    python scripts/check_setup.py ~/my-clipper

Checks config.yaml, the workflows, assets, and (when TWITCH_CLIENT_ID and
TWITCH_CLIENT_SECRET are set in the environment) that every Twitch login
exists and whether it has VODs. Never prints secret values.
"""
import os
import re
import sys

import yaml

ok = True


def bad(msg):
    global ok
    ok = False
    print("  ✗ " + msg)


def good(msg):
    print("  ✓ " + msg)


repo = os.path.abspath(os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else "."))
cfg = yaml.safe_load(open(os.path.join(repo, "config.yaml")))
print("config.yaml")
if not cfg.get("channel_name") or "YOUR CHANNEL" in str(cfg["channel_name"]).upper():
    bad("channel_name is still the placeholder")
else:
    good(f"channel: {cfg['channel_name']}")
logins = [s.get("login", "") for s in cfg.get("streamers", [])]
if not logins or "examplestreamer" in logins:
    bad("streamers list is empty or still the example")
else:
    good(f"{len(logins)} streamer(s): {', '.join(logins)}")
times = cfg.get("post_times") or []
if len(times) < len(logins):
    print(f"  ! {len(times)} posting time(s) for {len(logins)} streamers: not everyone gets a Short every day")
if len(times) > cfg.get("max_uploads_per_day", 6):
    bad("more post_times than max_uploads_per_day (YouTube quota allows about 6 uploads a day)")
from zoneinfo import ZoneInfo
ZoneInfo(cfg.get("post_timezone", "America/New_York"))
good(f"posting at {', '.join(times)} {cfg.get('post_timezone')}")

print("workflows")
wf = open(os.path.join(repo, ".github", "workflows", "daily.yml")).read()
m = re.search(r"options: \[all([^\]]*)\]", wf)
listed = [x.strip() for x in (m.group(1) if m else "").split(",") if x.strip()]
if sorted(listed) != sorted(logins):
    bad("daily.yml streamer choices don't match config.yaml (re-run scripts/configure.py)")
else:
    good("daily.yml streamer choices match")
for w in ("daily.yml", "clip-now.yml", "watcher.yml", "stats.yml"):
    if not os.path.exists(os.path.join(repo, ".github", "workflows", w)):
        bad(f"missing .github/workflows/{w}")

print("assets")
for f in ("InterDisplay-Black.otf", "InterDisplay-SemiBold.otf", "face_detection_yunet_2023mar.onnx", "logo.png"):
    (good if os.path.exists(os.path.join(repo, "assets", f)) else bad)(f"assets/{f}")
for l in logins:
    if not os.path.exists(os.path.join(repo, "assets", "streamers", f"{l}.png")):
        print(f"  ! no picture for {l} (the Short shows just the name; run scripts/streamer_pics.py)")

for f in ("README.md", "PRIVACY.md", "TERMS.md"):
    txt = open(os.path.join(repo, f)).read() if os.path.exists(os.path.join(repo, f)) else ""
    if not txt or "{{" in txt:
        bad(f"{f} missing or not filled in")

cid, secret = os.environ.get("TWITCH_CLIENT_ID"), os.environ.get("TWITCH_CLIENT_SECRET")
if cid and secret:
    import requests
    print("twitch")
    try:
        tok = requests.post("https://id.twitch.tv/oauth2/token", timeout=30, data={
            "client_id": cid, "client_secret": secret, "grant_type": "client_credentials"}).json()["access_token"]
        h = {"Client-ID": cid, "Authorization": f"Bearer {tok}"}
        users = requests.get("https://api.twitch.tv/helix/users", headers=h, timeout=30,
                             params=[("login", l) for l in logins]).json()["data"]
        ids = {u["login"].lower(): u["id"] for u in users}
        good("Twitch keys work")
        for l in logins:
            if l.lower() not in ids:
                bad(f"{l}: no such Twitch user")
                continue
            v = requests.get("https://api.twitch.tv/helix/videos", headers=h, timeout=30,
                             params={"user_id": ids[l.lower()], "type": "archive", "first": 1}).json()["data"]
            (good if v else lambda m: print("  ! " + m))(f"{l}: " + ("has VODs" if v else "no public VODs (only their Twitch clips can be used; no live clipping)"))
    except Exception as e:
        bad(f"Twitch check failed: {e}")
else:
    print("(set TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET to also check the Twitch logins)")

print("\nAll good." if ok else "\nFix the ✗ items above.")
sys.exit(0 if ok else 1)
