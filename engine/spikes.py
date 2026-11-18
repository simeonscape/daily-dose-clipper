"""Find the moments where a streamer's chat explodes.

Input is a chat file from TwitchDownloaderCLI (`chatdownload`, JSON format).
Output is a ranked list of spikes: the second in the VOD where chat peaked,
plus a score and the messages around it (used later to write the hook).
"""
import json
import re
from dataclasses import dataclass, field

# Messages that mean "something just happened". Weighted higher than normal chat.
HYPE = re.compile(
    r"\b(KEKW|OMEGALUL|LUL|LULW|LMAO+|LMFAO|ICANT|ICANT|PogU?|POGGERS|W+|L+|CLIP|"
    r"monkaS|monkaW|Aware|Clueless|NAHH+|NAH+|BRO+|WTF|HOLY|DEAD|💀|😂|🤣|😭)\b",
    re.IGNORECASE,
)


@dataclass
class Spike:
    t: float                     # seconds into the VOD where chat peaked
    score: float                 # how far above normal chat was
    messages: list = field(default_factory=list)


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

    Chat is counted in small bins, with hype messages counted 3x. A spike is
    a 10 s window whose activity is far above the median of the 5 minutes
    around it (robust z-score), so busy streams and quiet streams both work.
    """
    if not chat:
        return []
    end = chat[-1][0]
    n = int(end // bin_s) + 1
    bins = [0.0] * n
    for t, body in chat:
        bins[int(t // bin_s)] += 3.0 if HYPE.search(body) else 1.0

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
            msgs = [m for (tt, m) in chat if t - 15 <= tt <= t + 15][:60]
            picked.append(Spike(t=t, score=round(z, 2), messages=msgs))
    return picked


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
        print(f"spike at {s.t:8.1f}s  score {s.score:6.2f}  clip {a:.0f}-{b:.0f}s")
