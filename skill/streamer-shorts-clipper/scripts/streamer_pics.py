"""Download each streamer's Twitch profile picture into assets/streamers/<login>.png
(the round badge at the top of every Short).

    TWITCH_CLIENT_ID=... TWITCH_CLIENT_SECRET=... python scripts/streamer_pics.py ~/my-clipper [--force]

Reads the streamer list and brand colour from the repo's config.yaml. Pictures
already there are kept unless --force. For the cut-out look (subject on the
brand colour with a white outline) run scripts/brand_pfp.py on a photo instead.
Needs: requests, pyyaml, pillow.
"""
import io
import os
import sys

import requests
import yaml
from PIL import Image


def token(cid, secret):
    r = requests.post("https://id.twitch.tv/oauth2/token", timeout=30, data={
        "client_id": cid, "client_secret": secret, "grant_type": "client_credentials"})
    r.raise_for_status()
    return r.json()["access_token"]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    force = "--force" in sys.argv
    repo = os.path.abspath(os.path.expanduser(args[0] if args else "."))
    cid, secret = os.environ.get("TWITCH_CLIENT_ID"), os.environ.get("TWITCH_CLIENT_SECRET")
    if not (cid and secret):
        sys.exit("Set TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET first (from dev.twitch.tv/console/apps).")
    cfg = yaml.safe_load(open(os.path.join(repo, "config.yaml")))
    color = (cfg.get("brand") or {}).get("color", "#D97757").lstrip("#")
    bg = tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))
    logins = [s["login"] for s in cfg["streamers"]]
    h = {"Client-ID": cid, "Authorization": f"Bearer {token(cid, secret)}"}
    r = requests.get("https://api.twitch.tv/helix/users", headers=h, timeout=30,
                     params=[("login", l) for l in logins])
    r.raise_for_status()
    found = {u["login"].lower(): u for u in r.json()["data"]}
    outdir = os.path.join(repo, "assets", "streamers")
    os.makedirs(outdir, exist_ok=True)
    for login in logins:
        u = found.get(login.lower())
        if not u:
            print(f"  {login}: no such Twitch user. Check the login in config.yaml.")
            continue
        dst = os.path.join(outdir, f"{login}.png")
        if os.path.exists(dst) and not force:
            print(f"  {login}: picture already there (use --force to replace)")
            continue
        img = Image.open(io.BytesIO(requests.get(u["profile_image_url"], timeout=30).content)).convert("RGBA")
        canvas = Image.new("RGBA", img.size, bg + (255,))
        canvas.alpha_composite(img)
        canvas.convert("RGB").resize((240, 240), Image.LANCZOS).save(dst)
        print(f"  {login}: saved ({u['display_name']})")


if __name__ == "__main__":
    main()
