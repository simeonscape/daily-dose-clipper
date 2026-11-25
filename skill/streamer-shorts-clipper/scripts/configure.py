"""Turn the bundled template into a ready-to-push clipper repo for one channel.

    python scripts/configure.py --out ~/my-clipper \
        --channel "Daily Clips of Streamers" \
        --streamers "CaseOh=caseoh_,Jynxzi=jynxzi" \
        --owner "Your Name" --contact "you@example.com" \
        --repo "your-github-user/my-clipper" \
        [--color "#D97757"] [--timezone America/New_York] \
        [--times "12:00,15:00,17:00,19:00,21:00"] [--privacy public|private]

It copies template/ to --out (or updates it in place if it already exists),
then fills in config.yaml, the streamer list in the "Daily clips" workflow,
README / PRIVACY / TERMS (needed for Google's API audit), and a placeholder
logo if assets/logo.png is missing. Needs only Python 3 + Pillow + PyYAML.
Running it again updates the same files; options you leave out keep their
current values, and --streamers replaces the whole list.
"""
import argparse
import os
import re
import shutil
import sys
from datetime import date

SKILL = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(SKILL, "template")
TEMPLATES = os.path.join(SKILL, "templates")


def parse_streamers(text):
    out = []
    for part in [p.strip() for p in text.split(",") if p.strip()]:
        if "=" in part:
            name, login = (x.strip() for x in part.split("=", 1))
        else:
            name, login = part, part
        login = login.lower().lstrip("@").replace("https://twitch.tv/", "").replace("https://www.twitch.tv/", "")
        if not re.fullmatch(r"[a-z0-9_]{3,25}", login):
            sys.exit(f"'{login}' doesn't look like a Twitch login (the name in twitch.tv/<login>).")
        out.append((name, login))
    if not out:
        sys.exit("Give at least one streamer, e.g. --streamers \"CaseOh=caseoh_\"")
    return out


def yq(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def fill_config(path, a, streamers):
    s = open(path, encoding="utf-8").read()
    s = re.sub(r"^channel_name:.*$", f"channel_name: {yq(a.channel)}", s, count=1, flags=re.M)
    if a.color:
        s = re.sub(r'^(  color: ).*$', rf'\g<1>{yq(a.color)}', s, count=1, flags=re.M)
    if a.timezone:
        s = re.sub(r"^post_timezone:.*$", f"post_timezone: {a.timezone}", s, count=1, flags=re.M)
    if a.times:
        times = ", ".join(yq(t.strip()) for t in a.times.split(",") if t.strip())
        s = re.sub(r"^post_times:.*$", f"post_times: [{times}]", s, count=1, flags=re.M)
    if a.privacy:
        s = re.sub(r"^privacy: \w+", f"privacy: {a.privacy}", s, count=1, flags=re.M)
    head = s[:s.index("\nstreamers:\n") + 1]
    block = "streamers:\n"
    for name, login in streamers:
        block += f"  - name: {yq(name)}\n    login: {login}\n    platform: twitch\n"
    open(path, "w", encoding="utf-8").write(head + block)


def fill_workflow(path, streamers):
    s = open(path, encoding="utf-8").read()
    opts = ", ".join(["all"] + [login for _, login in streamers])
    s = re.sub(r"options: \[all[^\]]*\].*$", f"options: [{opts}]", s, count=1, flags=re.M)
    n = len(streamers)
    counts = sorted({c for c in (1, 2, 3, 5, n) if c <= n})
    s = re.sub(r'options: \["1"[^\]]*\]', "options: [" + ", ".join(f'"{c}"' for c in counts) + "]", s, count=1)
    open(path, "w", encoding="utf-8").write(s)


def fill_text(name, out, values):
    s = open(os.path.join(TEMPLATES, name), encoding="utf-8").read()
    for k, v in values.items():
        s = s.replace("{{" + k + "}}", v)
    left = re.findall(r"\{\{[A-Z_]+\}\}", s)
    if left:
        sys.exit(f"{name}: unfilled {left}")
    open(os.path.join(out, name), "w", encoding="utf-8").write(s)


def placeholder_logo(path, channel, color):
    from PIL import Image, ImageDraw, ImageFont
    rgb = tuple(int(color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    img = Image.new("RGB", (800, 800), rgb)
    d = ImageDraw.Draw(img)
    words = [w for w in re.split(r"\s+", channel) if w and w[0].isalnum()]
    initials = "".join(w[0] for w in words[:3]).upper() or "C"
    font = ImageFont.truetype(os.path.join(os.path.dirname(path), "InterDisplay-Black.otf"), 300 if len(initials) < 3 else 230)
    d.text((400, 400), initials, font=font, fill=(255, 255, 255), anchor="mm")
    img.save(path)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="folder for the new repo")
    ap.add_argument("--channel", required=True, help="YouTube channel name")
    ap.add_argument("--streamers", required=True, help='"Name=login,Name2=login2"')
    ap.add_argument("--owner", required=True, help="your name, for the Terms page")
    ap.add_argument("--contact", required=True, help="contact email or URL for Privacy/Terms")
    ap.add_argument("--repo", required=True, help="GitHub owner/name of the new repo")
    ap.add_argument("--color", help="brand colour, hex (new repo default #D97757)")
    ap.add_argument("--timezone", help="posting time zone (new repo default America/New_York)")
    ap.add_argument("--times", help='posting times "HH:MM,HH:MM" (new repo default 12:00,15:00,17:00,19:00,21:00)')
    ap.add_argument("--privacy", choices=["public", "private"], help="upload privacy (new repo default public)")
    a = ap.parse_args()
    # Options left out keep what the repo already has (the template's defaults for a new repo).
    if a.color:
        if not re.fullmatch(r"#?[0-9a-fA-F]{6}", a.color):
            sys.exit("--color must be a hex colour like #D97757")
        a.color = "#" + a.color.lstrip("#").upper()
    from zoneinfo import ZoneInfo
    if a.timezone:
        ZoneInfo(a.timezone)  # raises if unknown
    for t in (a.times or "").split(","):
        if not t.strip():
            continue
        if not re.fullmatch(r"\d{1,2}:\d{2}", t.strip()):
            sys.exit(f"Bad time '{t}', use HH:MM")
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", a.repo):
        sys.exit("--repo must be owner/name")

    streamers = parse_streamers(a.streamers)
    out = os.path.abspath(os.path.expanduser(a.out))
    fresh = not os.path.exists(os.path.join(out, "run.py"))
    if fresh:
        shutil.copytree(TEMPLATE, out, dirs_exist_ok=True)
    fill_config(os.path.join(out, "config.yaml"), a, streamers)
    fill_workflow(os.path.join(out, ".github", "workflows", "daily.yml"), streamers)
    import yaml
    cfg = yaml.safe_load(open(os.path.join(out, "config.yaml"), encoding="utf-8"))
    a.color = (cfg.get("brand") or {}).get("color", "#D97757")
    a.timezone = cfg.get("post_timezone", "America/New_York")
    a.times = ",".join(str(t) for t in cfg.get("post_times", []))
    values = {
        "CHANNEL": a.channel, "OWNER": a.owner, "CONTACT": a.contact,
        "REPO": a.repo, "DATE": date.today().strftime("%d %B %Y").lstrip("0"),
        "TIMEZONE": a.timezone, "TIMES": ", ".join(t.strip() for t in a.times.split(",")),
        "STREAMERS": ", ".join(n for n, _ in streamers),
        "FIRST_LOGIN": streamers[0][1],
    }
    for name in ("README.md", "PRIVACY.md", "TERMS.md"):
        fill_text(name, out, values)
    logo = os.path.join(out, "assets", "logo.png")
    if not os.path.exists(logo):
        placeholder_logo(logo, a.channel, a.color)
        print("Made a placeholder logo (assets/logo.png). Replace it with your channel's icon any time.")
    os.makedirs(os.path.join(out, "assets", "streamers"), exist_ok=True)
    missing = [l for _, l in streamers if not os.path.exists(os.path.join(out, "assets", "streamers", f"{l}.png"))]
    print(f"{'Created' if fresh else 'Updated'} {out}")
    print(f"  channel: {a.channel} · colour {a.color} · {len(streamers)} streamer(s) · times {values['TIMES']} {a.timezone}")
    if missing:
        print("  no picture yet for: " + ", ".join(missing) + " (run scripts/streamer_pics.py)")


if __name__ == "__main__":
    main()
