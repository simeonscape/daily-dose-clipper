"""Find the moments where a streamer's chat explodes.

Input is a chat file from TwitchDownloaderCLI (`chatdownload`, JSON format).
Output is a ranked list of spikes: the second in the VOD where chat peaked,
plus a score and the messages around it (used later to write the hook).
"""
import json
import re
from dataclasses import dataclass, field

# What chat spams when a moment is worth clipping. Emoji have no word
# boundaries, so they're matched on their own.
LOL = re.compile(r"\b(KEKW|KEKL|OMEGALUL|LULW?|LMA+O+|LMFAO|ICANT|HA(HA)+|XD+|pepeLaugh|"
                 r"DEAD|CRYING|DYING)\b|😂|🤣|💀|😭", re.IGNORECASE)
CLIP_IT = re.compile(r"\bCLIP+\b|\bCLIP+(ED|PED)\b|CLIP\s*(IT|THAT|THIS)|\bclipp+", re.IGNORECASE)
HYPE = re.compile(r"\b(Pog\w*|POGGERS|W+|L+|monka\w*|NAH+|BRO+|WTF|HOLY|OMG|NO WAY|WHAT+|INSANE)\b|🔥|😳|😱",
                  re.IGNORECASE)

# How much each kind of message counts toward a spike
WEIGHTS = {"clip": 5.0, "lol": 4.0, "hype": 2.0, "chat": 1.0}


def kind(body):
    if CLIP_IT.search(body):
        return "clip"
    if LOL.search(body):
        return "lol"
    if HYPE.search(body):
        return "hype"
    return "chat"


@dataclass
class Spike:
    t: float                     # seconds into the VOD where chat peaked
    score: float                 # how far above normal chat was (weighted)
    messages: list = field(default_factory=list)
    lol: int = 0                 # LOL-type messages around the peak
    clip: int = 0                # "CLIP IT" messages around the peak
    viral: float = 0.0           # ranking score, comparable between streamers


def load_chat(path):
    """Return a list of (seconds, text) from a TwitchDownloader chat JSON."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    out = []
    for c in data.get("comments", []):
        t = c.get("content_offset_seconds")
        body = (c.get("message") or {}).get("body", "")
        if t is not None:
            out.append((float(t), body))
    out.sort()
    return out


def find_spikes(chat, bin_s=2.0, window_s=10.0, baseline_s=300.0,
                min_gap_s=120.0, top=5, min_score=3.0):
    """Rank chat spikes.

    Chat is counted in small bins, weighted by what people type: "CLIP IT" 5x,
    LOL spam (KEKW, OMEGALUL, LMAO, 💀...) 4x, other hype 2x, normal chat 1x.
    A spike is a 10 s window far above the median of the 5 minutes around it
    (robust z-score), so busy and quiet streams compare fairly.

    `viral` ranks spikes across streamers: the z-score, boosted by how much of
    chat around the peak was LOL or CLIP IT.
    """
    if not chat:
        return []
    end = chat[-1][0]
    n = int(end // bin_s) + 1
    bins = [0.0] * n
    for t, body in chat:
        bins[int(t // bin_s)] += WEIGHTS[kind(body)]

    w = max(1, int(window_s // bin_s))
    act = [sum(bins[i:i + w]) for i in range(n)]

    b = int(baseline_s // bin_s)
    scored = []
    for i in range(0, n, 1):
        lo, hi = max(0, i - b), min(n, i + b)
        ref = sorted(act[lo:hi])
        med = ref[len(ref) // 2]
        mad = sorted(abs(x - med) for x in ref)[len(ref) // 2] or 1.0
        z = (act[i] - med) / (1.4826 * mad)
        scored.append((z, i))

    scored.sort(reverse=True)
    picked = []
    for z, i in scored:
        if z < min_score or len(picked) >= top:
            break
        t = (i + w / 2) * bin_s
        if all(abs(t - p.t) >= min_gap_s for p in picked):
            around = [m for (tt, m) in chat if t - 15 <= tt <= t + 15]
            kinds = [kind(m) for m in around]
            lol, clip = kinds.count("lol"), kinds.count("clip")
            share = (lol + clip) / max(1, len(around))
            viral = z * (1 + 2 * share) + 0.5 * min(clip, 20)
            picked.append(Spike(t=t, score=round(z, 2), messages=around[:60],
                                lol=lol, clip=clip, viral=round(viral, 2)))
    return picked


def chat_features(chat, t, around_s=15.0, bin_s=2.0, window_s=10.0):
    """How hard chat reacted to a moment at `t` (seconds), compared with the rest
    of the chat we have (ideally a few minutes either side of it).

    Returns z (how far the busiest 10 s near t is above normal), the share of
    LOL / CLIP IT messages around it, and the raw counts. Works on a small
    chat window, so it can rate a clip's moment without the whole VOD's chat."""
    out = {"z": 0.0, "lol_share": 0.0, "clip_it": 0, "hype": 0, "messages": []}
    if not chat:
        return out
    chat = sorted(chat)
    t0 = chat[0][0]
    n = int((chat[-1][0] - t0) // bin_s) + 1
    bins = [0.0] * n
    for tt, body in chat:
        bins[int((tt - t0) // bin_s)] += WEIGHTS[kind(body)]
    w = max(1, int(window_s // bin_s))
    act = [sum(bins[i:i + w]) for i in range(n)]
    ref = sorted(act)
    med = ref[len(ref) // 2]
    mad = sorted(abs(x - med) for x in ref)[len(ref) // 2] or 1.0
    # chat reacts after the moment, so look a bit later than t
    lo, hi = int((t - around_s - t0) // bin_s), int((t + around_s + 8 - t0) // bin_s)
    near = act[max(0, lo):max(0, min(n, hi))]
    if near:
        out["z"] = round((max(near) - med) / (1.4826 * mad), 2)
    around = [m for (tt, m) in chat if t - around_s <= tt <= t + around_s + 8]
    kinds = [kind(m) for m in around]
    out["lol_share"] = round((kinds.count("lol") + kinds.count("clip")) / max(1, len(around)), 3)
    out["clip_it"] = kinds.count("clip")
    out["hype"] = kinds.count("hype")
    out["messages"] = around[:60]
    return out


def peak_time(chat, t, search_s=45.0, bin_s=2.0, window_s=10.0, min_z=2.0):
    """The highest chat spike near `t`: the middle of the busiest `window_s`
    seconds (weighted: CLIP IT and LOL count most) within `search_s` of `t`.

    A Twitch clip only says roughly where a moment is; chat's biggest burst
    nearby is the real peak. Returns (seconds into the VOD where chat peaked,
    its z score), or (None, z) when nothing near `t` stands out (z < min_z).
    Chat reacts a few seconds after the moment, so subtract chat_delay_s."""
    if not chat:
        return None, 0.0
    chat = sorted(chat)
    t0 = chat[0][0]
    n = int((chat[-1][0] - t0) // bin_s) + 1
    bins = [0.0] * n
    for tt, body in chat:
        bins[int((tt - t0) // bin_s)] += WEIGHTS[kind(body)]
    w = max(1, int(window_s // bin_s))
    act = [sum(bins[i:i + w]) for i in range(n)]
    ref = sorted(act)
    med = ref[len(ref) // 2]
    mad = sorted(abs(x - med) for x in ref)[len(ref) // 2] or 1.0
    lo = max(0, int((t - search_s - t0) // bin_s))
    hi = min(n, int((t + search_s + 8 - t0) // bin_s) + 1)
    if hi <= lo:
        return None, 0.0
    i = max(range(lo, hi), key=lambda k: act[k])
    z = round((act[i] - med) / (1.4826 * mad), 2)
    if z < min_z:
        return None, z
    return round(t0 + i * bin_s + window_s / 2, 2), z


def clip_window(spike, before=30.0, after=30.0, chat_delay=6.0, vod_len=None):
    """The clip: 30 s before the spike to 30 s after it.

    Chat reacts a few seconds after the moment on stream, so the window is
    shifted back by `chat_delay` seconds.
    """
    start = max(0.0, spike.t - chat_delay - before)
    end = spike.t - chat_delay + after
    if vod_len:
        end = min(end, vod_len)
    return start, end


if __name__ == "__main__":
    import sys
    for s in find_spikes(load_chat(sys.argv[1])):
        a, b = clip_window(s)
        print(f"spike at {s.t:8.1f}s  viral {s.viral:6.2f}  z {s.score:5.2f}  "
              f"LOL {s.lol:4d}  CLIP IT {s.clip:3d}  clip {a:.0f}-{b:.0f}s")
