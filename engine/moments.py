"""Find a streamer's candidate moments and rank them by virality.

Where candidates come from:
  1. Their most-viewed Twitch clips from the last few days (what viewers saved
     and shared). Clips of the same moment are merged and their views added.
     This includes clips made during a stream that is still live.
  2. If they have no recent clips: chat spikes in their latest stream
     (the one still being recorded counts too when they're live).

How the winner is picked: the top few candidates get chat fetched around them,
then engine/virality.py gives each one score from views, how many clips,
the chat explosion, laughter, CLIP IT spam and how recent it is.
When the top scores are close and Claude is set up (`ai:` in config.yaml),
one small call shows Claude the top 2-3 (numbers, the clip title and a few
chat lines) and lets it pick the one that is actually viral, not just busy.
"""
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone

import ai
import spikes
import state as st
import twitch
import virality


@dataclass
class Moment:
    source: str                  # "clips", "chat" or "live"
    vod: dict = None             # VOD it's in, if the VOD is still up
    t: float = 0.0               # seconds into the VOD
    clip_id: str = None          # for clips whose VOD is gone: download the clip itself
    title: str = ""              # title viewers gave the clip
    views: int = 0               # total views of the clips of this moment
    clips: int = 1               # how many clips were made of this moment
    clip_ids: list = field(default_factory=list)
    created_at: str = None       # when it happened (ISO)
    chat: dict = field(default_factory=dict)      # spikes.chat_features()
    messages: list = field(default_factory=list)  # chat lines around it
    viral: float = 0.0           # final score, see virality.py
    signals: dict = field(default_factory=dict)
    live: bool = False           # the VOD is still being recorded
    clip_ranges: list = field(default_factory=list)   # [(start, end)] in the VOD of viewers' clips
    key_override: str = None     # live moments without a VOD: "live:<login>:<stream id>"

    @property
    def key(self):
        if self.key_override:
            return self.key_override
        return self.vod["id"] if self.vod else f"clip:{self.clip_id}"

    def describe(self):
        bits = [f"viral {self.viral:.2f}"]
        if self.views:
            bits.append(f"{self.views:,} views/{self.clips} clips")
        if self.chat:
            bits.append(f"chat z {self.chat.get('z', 0):.1f}, LOL {self.chat.get('lol_share', 0):.0%}")
        return ", ".join(bits) + (f' "{self.title}"' if self.title else "")


def clip_moments(s, cfg, live_vod=None):
    """The streamer's recent clips, grouped by moment."""
    clips = twitch.top_clips(s["login"], days=cfg.get("clip_days", 7))
    vods, moments = {}, []
    for c in clips:
        if c["view_count"] < cfg.get("min_clip_views", 200):
            continue
        if c.get("video_id") and c.get("vod_offset") is not None:
            # the moment is usually near the end of a clip
            t = c["vod_offset"] + 0.7 * c["duration"]
            same = next((m for m in moments if m.vod and m.vod["id"] == c["video_id"] and abs(m.t - t) < 40), None)
            if same:
                same.views += c["view_count"]; same.clips += 1; same.clip_ids.append(c["id"])
                same.clip_ranges.append((c["vod_offset"], c["vod_offset"] + c["duration"]))
                continue
            vods.setdefault(c["video_id"], {"id": c["video_id"], "duration": None})
            moments.append(Moment(source="clips", vod=vods[c["video_id"]], t=t, title=c["title"],
                                  views=c["view_count"], clip_id=c["id"], clip_ids=[c["id"]],
                                  clip_ranges=[(c["vod_offset"], c["vod_offset"] + c["duration"])],
                                  created_at=c.get("created_at"),
                                  live=bool(live_vod and c["video_id"] == live_vod["id"])))
        else:
            moments.append(Moment(source="clips", clip_id=c["id"], title=c["title"], views=c["view_count"],
                                  clip_ids=[c["id"]], created_at=c.get("created_at")))
    return moments


def chat_moments(s, cfg, workdir, live_vod=None):
    """Chat spikes in the latest stream (the live one if they're live now)."""
    vods = twitch.latest_vods(s["login"], n=1)
    vod = vods[0] if vods else None
    if not vod:
        return []
    is_live = bool(live_vod and live_vod["id"] == vod["id"])
    os.makedirs(workdir, exist_ok=True)
    chat_path = os.path.join(workdir, f"chat_{vod['id']}.json")
    if is_live or not os.path.exists(chat_path):    # a live VOD keeps growing, fetch it fresh
        print(f"Downloading chat for {s['name']}, VOD {vod['id']}{' (live)' if is_live else ''} ...")
        twitch.download_chat(vod["id"], chat_path)
    chat = spikes.load_chat(chat_path)
    found = spikes.find_spikes(chat, top=10, min_score=cfg.get("min_spike_score", 3.0))
    out = []
    for sp in found:
        m = Moment(source="chat", vod=vod, t=sp.t - cfg.get("chat_delay_s", 6), messages=sp.messages,
                   created_at=vod.get("created_at"), live=is_live)
        m.chat = spikes.chat_features(chat, m.t)
        out.append(m)
    return out


def add_chat(m, workdir, around=180):
    """Fetch chat a few minutes around a clip's moment and rate chat's reaction."""
    if not m.vod or m.chat:
        return
    a = max(0, int(m.t - around))
    os.makedirs(workdir, exist_ok=True)
    path = os.path.join(workdir, f"chatwin_{m.vod['id']}_{a}.json")
    try:
        if not os.path.exists(path) or m.live:
            twitch.download_chat_window(m.vod["id"], a, int(m.t + around), path)
        chat = spikes.load_chat(path)
    except Exception as e:
        print(f"  (no chat for {m.key} @ {m.t:.0f}s: {e})")
        return
    m.chat = spikes.chat_features(chat, m.t)
    m.messages = m.chat.get("messages", [])


def candidates(s, cfg, state, workdir):
    """All fresh moments for a streamer, best first."""
    live_vod = None
    try:
        stream = twitch.live_streams([s["login"]]).get(s["login"].lower())
        if stream:
            live_vod = twitch.live_vod(s["login"], stream)
            print(f"{s['name']} is live now" + (", clips from this stream count too" if live_vod else
                                                 " (no VOD being recorded, older streams only)"))
    except Exception as e:
        print(f"Couldn't check if {s['name']} is live ({e})")
    try:
        found = clip_moments(s, cfg, live_vod)
    except Exception as e:
        print(f"Couldn't read {s['name']}'s clips ({e}), using chat instead")
        found = []
    fresh = [m for m in found if not st.is_repeat(state, s["name"], m)]
    if found and len(fresh) < len(found):
        print(f"{s['name']}: skipped {len(found) - len(fresh)} moment(s) that were already posted")
    if not fresh:
        print(f"{s['name']}: no fresh clips, looking at chat spikes")
        fresh = [m for m in chat_moments(s, cfg, workdir, live_vod) if not st.is_repeat(state, s["name"], m)]

    vc = cfg.get("virality", {}) or {}
    ranked = virality.rank(fresh, cfg)
    # chat around the moment for the shortlist, then rank again with it
    for m in ranked[: vc.get("shortlist", 5)]:
        add_chat(m, workdir)
    ranked = virality.rank(ranked, cfg)
    ranked = ai_pick(s, ranked, cfg)
    if ranked:
        print(f"{s['name']}: {len(ranked)} candidate moments. Top: "
              + " | ".join(m.describe() for m in ranked[:3]))
    return ranked


PICK_PROMPT = """Streamer: {streamer}. Which of these moments from their streams would make the most viral YouTube Short?
Busy chat alone isn't enough: look for a real funny, shocking, impressive or chaotic moment that a new viewer would get.
Numbers: views/clips = Twitch clips of it, chat_z = how far chat jumped above normal,
laugh = share of chat laughing, clip_it = CLIP IT messages, age_h = hours ago.
<moments>
{moments}
</moments>
Reply: {{"pick": number of the best moment, "why": "max 12 words"}}"""


def _age_h(m):
    try:
        then = datetime.fromisoformat(m.created_at.replace("Z", "+00:00"))
        return round((datetime.now(timezone.utc) - then).total_seconds() / 3600)
    except (AttributeError, ValueError):
        return "?"


def ai_pick(s, ranked, cfg):
    """Let Claude choose among the top few when the scores are too close to call.
    Returns `ranked` with Claude's pick first (unchanged if Claude isn't asked)."""
    a = ai.settings(cfg)
    top = ranked[: max(2, int(a["pick_top"]))]
    if len(top) < 2 or not ai.available(cfg, "pick"):
        return ranked
    best = top[0].viral or 1e-9
    top = [m for m in top if m.viral >= best * (1 - float(a["pick_when_within"]))]
    if len(top) < 2:
        return ranked          # a clear winner: no need to ask
    lines = []
    for i, m in enumerate(top, 1):
        c = m.chat or {}
        chat = " / ".join(ai.short(x, 40) for x in (m.messages or [])[:8])
        lines.append(f'{i}. title="{ai.short(m.title, 80)}" views={m.views} clips={m.clips} '
                     f'chat_z={c.get("z", 0)} laugh={c.get("lol_share", 0):.0%} clip_it={c.get("clip_it", 0)} '
                     f'age_h={_age_h(m)} chat: {chat or "(none)"}')
    prompt = PICK_PROMPT.format(streamer=s["name"], moments="\n".join(lines))

    def check(ans):
        assert 1 <= int(ans["pick"]) <= len(top)

    ans = ai.ask("pick", prompt, cfg, check=check)
    if not ans:
        return ranked
    win = top[int(ans["pick"]) - 1]
    print(f"{s['name']}: Claude picked moment {ans['pick']} of {len(top)} ({ai.short(ans.get('why', ''), 80)})")
    return [win] + [m for m in ranked if m is not win]
