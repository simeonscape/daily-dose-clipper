"""Rank moments by how viral they are, then find the best part of the stream
around the winner.

Two steps:

1. rank(): every candidate moment gets one score from several signals, each
   scaled to roughly 0..1 and weighted (weights in config.yaml, `virality:`):
     views     - total views of the Twitch clips of this moment (log scale)
     clips     - how many different viewers clipped it
     chat      - how far chat activity jumped above normal (robust z-score)
     laughter  - share of chat that was LOL spam (KEKW, 💀, LMAO...)
     clip_it   - how many people typed CLIP IT
     recency   - newer moments are worth more (half-life in hours)
   Chat signals need a few minutes of chat around the moment; run.py fetches
   that for the shortlist only, so ranking stays fast.

2. refine(): after the video around the winner is downloaded (with some extra
   on both sides), the stream's loudness and chat are combined into an
   "excitement" curve. The Short is re-centred on its real peak (screams,
   laughter, chat exploding), and the start and end are moved to the nearest
   quiet point so a sentence isn't cut in half.
"""
import math
import subprocess
from datetime import datetime, timezone

import numpy as np

DEFAULT_WEIGHTS = {"views": 0.40, "clips": 0.10, "chat": 0.25, "laughter": 0.10,
                   "clip_it": 0.05, "recency": 0.10}


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def features(m, now=None, half_life_h=36.0):
    """0..1 signals for one Moment (see run.py / engine/moments.py)."""
    now = now or datetime.now(timezone.utc)
    f = {
        "views": _clamp(math.log10(1 + m.views) / 5.0),           # 100k views = 1
        "clips": _clamp((m.clips - 1) / 9.0) if m.views else 0.0,   # 10 clips = 1
        "chat": _clamp(m.chat.get("z", 0.0) / 12.0),                # z of 12 = 1
        "laughter": _clamp(m.chat.get("lol_share", 0.0) / 0.6),
        "clip_it": _clamp(m.chat.get("clip_it", 0) / 15.0),
        "recency": 0.5,
    }
    if m.created_at:
        try:
            age_h = (now - datetime.fromisoformat(m.created_at.replace("Z", "+00:00"))).total_seconds() / 3600
            f["recency"] = _clamp(0.5 ** (max(0.0, age_h) / half_life_h))
        except ValueError:
            pass
    return f


def score(m, weights=None, now=None, half_life_h=36.0):
    w = dict(DEFAULT_WEIGHTS, **(weights or {}))
    f = features(m, now, half_life_h)
    m.signals = {k: round(v, 3) for k, v in f.items()}
    m.viral = round(sum(w.get(k, 0) * v for k, v in f.items()), 4)
    return m.viral


def rank(moments, cfg=None):
    vc = (cfg or {}).get("virality", {}) or {}
    for m in moments:
        score(m, vc.get("weights"), half_life_h=vc.get("recency_half_life_h", 36.0))
    return sorted(moments, key=lambda m: -m.viral)


# ---- step 2: the best window inside the downloaded part --------------------

def loudness(video, step=0.5):
    """Loudness (dB) of the audio every `step` seconds."""
    pcm = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", video, "-vn", "-ac", "1",
                          "-ar", "8000", "-f", "s16le", "-"], capture_output=True, check=True).stdout
    a = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768.0
    n = int(8000 * step)
    if len(a) < n:
        return np.zeros(1)
    a = a[: len(a) // n * n].reshape(-1, n)
    return 20 * np.log10(np.sqrt((a ** 2).mean(1)) + 1e-5)


def _z(x):
    x = np.asarray(x, np.float32)
    med = np.median(x)
    mad = np.median(np.abs(x - med)) or 1.0
    return (x - med) / (1.4826 * mad)


def _smooth(x, k):
    if k <= 1 or len(x) < k:
        return x
    return np.convolve(x, np.ones(k) / k, mode="same")


def excitement(video, chat=None, seg_start=0.0, chat_delay=6.0, step=0.5):
    """Per-step excitement of a downloaded part: loud moments plus chat bursts.
    `chat` is [(seconds into the VOD, text)], `seg_start` where the part starts in the VOD."""
    from spikes import WEIGHTS, kind
    db = loudness(video, step)
    audio = np.clip(_z(db), -3, 6)
    curve = audio
    if chat:
        c = np.zeros(len(db))
        for t, body in chat:
            i = int((t - seg_start - chat_delay) / step)
            if 0 <= i < len(c):
                c[i] += WEIGHTS[kind(body)]
        c = _smooth(c, int(4 / step))
        if c.any():
            curve = 0.6 * audio + 0.4 * np.clip(_z(c), -3, 6)
    return _smooth(curve, int(3 / step)), db


def refine(video, moment_t, before, after, chat=None, seg_start=0.0, chat_delay=6.0,
           search_s=20.0, snap_s=2.0, step=0.5):
    """Best (start, end) in seconds of `video` for a Short of before+after seconds.

    `moment_t` is where we think the moment is (seconds into `video`). The peak
    of excitement within `search_s` of it becomes the new centre (the moment
    sits `before` seconds in), then both cuts move to the quietest point within
    `snap_s` so the Short doesn't start or end mid-word."""
    curve, db = excitement(video, chat, seg_start, chat_delay, step)
    n = len(curve)
    dur = n * step
    lo = max(0, int((moment_t - search_s) / step))
    hi = min(n, int((moment_t + search_s * 0.75) / step) + 1)
    peak_t = moment_t
    if hi > lo:
        # prefer peaks near the guess: a gentle pull toward moment_t
        idx = np.arange(lo, hi)
        pull = 1.0 - 0.3 * np.abs(idx * step - moment_t) / search_s
        peak_t = float(idx[int(np.argmax(curve[lo:hi] * pull))] * step)
    start = max(0.0, peak_t - before)
    end = min(dur, start + before + after)
    start = max(0.0, end - before - after) if end - start < before + after else start

    def quiet(t):
        a, b = max(0, int((t - snap_s) / step)), min(n, int((t + snap_s) / step) + 1)
        if b <= a:
            return t
        return float((a + int(np.argmin(db[a:b]))) * step)

    s2, e2 = quiet(start), quiet(end)
    e2 = min(e2, s2 + before + after)
    if e2 - s2 >= 0.8 * (before + after):
        start, end = s2, e2
    return round(start, 2), round(min(end, dur), 2), round(peak_t, 2)
