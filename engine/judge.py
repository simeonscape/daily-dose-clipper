"""The smart clipper: look at the whole stretch around a moment, work out
what actually happened and when, and choose the cut.

A Twitch clip or a chat spike only says roughly where something happened.
The real highlight can be 30+ seconds earlier (the setup) or later (the
payoff). So the engine downloads a wider stretch (`smart.context_before` /
`context_after` seconds around the moment) and judges it from:

  - what was said: the transcript, with a time for every word
  - what chat did: messages, laughter and CLIP IT every few seconds
  - how loud it got: screams, laughter, explosions
  - where viewers' own Twitch clips of it start and end

Two judges:
  1. Claude (when CLAUDE_CODE_OAUTH_TOKEN is set, see engine/ai.py). ONE
     small text-only call per Short (no frames, no tools) gets the cut AND the
     title, hook, first comment and layout, so nothing else needs asking.
  2. Built-in (always there, used if Claude isn't set up or fails): finds
     the most exciting point (loudness + chat, pulled toward the viewers'
     clips), starts at the beginning of a sentence before it so the setup
     is in, and ends once the excitement has died down after it.

Either way the Short is between `smart.min_len` and `smart.max_len` seconds.

Quality gate (`quality:` in config.yaml): in that same call Claude also rates
how viral the cut is, 1 to 10. Below `quality.min_score` the moment is skipped
(TooWeak) and run.py tries the next one, a limited number of times, so fewer
but stronger Shorts go out. The built-in judge can't tell funny from busy, so
it doesn't rate; when Claude isn't available the Short is made as before.
"""
import numpy as np

import ai
import virality


class TooWeak(Exception):
    """Claude rated the moment below quality.min_score: don't post it."""

    def __init__(self, score, summary="", why=""):
        self.score, self.summary, self.why = score, summary, why
        super().__init__(f"viral score {score:g}/10 is below the minimum"
                         + (f" ({why})" if why else ""))


def quality(cfg):
    q = {"enabled": True, "min_score": 6, "tries_per_streamer": 2, "tries_per_run": 3}
    q.update((cfg or {}).get("quality") or {})
    return q

PROMPT = """Streamer: {streamer}. Clip channel: "{channel}".
Below is a {dur:.0f}-second stretch of a livestream; all times are seconds from its start.
{clips}{clip_title}
Transcript with times:
<transcript>
{transcript}
</transcript>

Every {step:.0f} s: loudness (0 = normal, higher = louder), chat messages, laughing messages, CLIP IT messages:
<timeline>
t,loud,chat,laugh,clipit
{timeline}
</timeline>

Some of what chat typed:
<chat>
{chat}
</chat>
{recent}{layout}
1. This stretch may hold more than one moment. Find the single most viral one (biggest real
reaction, clearest payoff) and the cut that tells it best: start where the setup begins (enough for
a new viewer to get it, no dead air), include the payoff, end once the reaction is over. Chat reacts
a few seconds after things happen. Length {min_len:.0f} to {max_len:.0f} seconds.
2. Rate that cut honestly as a YouTube Short for people who don't know the streamer, "viral_score" 1-10:
9-10 = anyone would share it (huge reaction, shocking/hilarious payoff); 7-8 = strong, clearly funny or
wild; 5-6 = okay but needs context or the payoff is small; 1-4 = nothing really happens (talking,
greetings, waiting, ads, chat busy for no visible reason). Be strict: most stretches are 4-6.
3. Write for that cut: "title" (max 70 characters, includes {streamer}, curiosity-driven but true,
must not repeat or closely copy a recent title), "hook" (on-screen text for the first 2 seconds,
max 7 words, all caps, no emoji, no spoilers), "about" (one short line about the moment),
"first_comment" (a short question that gets replies).

Reply: {{"start": s, "end": s, "peak": s, "what_happened": "one sentence", "confidence": 0 to 1,
"viral_score": 1 to 10, "why_score": "max 12 words", "title": "...", "hook": "...", "about": "...", "first_comment": "..."{layout_key}}}"""

LAYOUTS = {
    "split": "facecam on top, the whole screen below (reaction and game both matter)",
    "pip": "the game big, following the action, small facecam in a corner (the game is the moment)",
    "cam": "only the camera, following the face (the streamer's reaction or talking is the moment)",
}


def _sentence_starts(words, gap=0.6):
    """Times where a new sentence (or a pause) starts."""
    out = [words[0][0]] if words else []
    for (s0, e0, w0), (s1, _, _) in zip(words, words[1:]):
        if s1 - e0 >= gap or w0.strip().endswith((".", "!", "?")):
            out.append(s1)
    return out


def _sentence_ends(words, gap=0.6):
    out = []
    for (s0, e0, w0), (s1, _, _) in zip(words, words[1:]):
        if s1 - e0 >= gap or w0.strip().endswith((".", "!", "?")):
            out.append(e0)
    if words:
        out.append(words[-1][1])
    return out


def builtin(curve, step, words, guess, clip_ranges, min_len, max_len, setup=18.0):
    """The heuristic judge. Returns (start, end, peak)."""
    n = len(curve)
    dur = n * step
    t = np.arange(n) * step
    prior = 0.55 + 0.45 * np.exp(-((t - guess) / 30.0) ** 2)
    for a, b in clip_ranges:
        prior = np.maximum(prior, np.where((t >= a - 5) & (t <= b + 5), 1.0, 0.0))
    score = (curve - curve.min() + 0.1) * prior
    peak = float(t[int(np.argmax(score))])

    # start: a sentence start roughly `setup` seconds before the peak
    starts = [s for s in _sentence_starts(words) if peak - max_len + 8 <= s <= peak - 5]
    start = min(starts, key=lambda s: abs((peak - s) - setup)) if starts else max(0.0, peak - setup)

    # end: once excitement has been back to normal for 3 s (at least 5 s after the peak)
    base = float(np.median(curve))
    calm_level = base + 0.25 * (float(curve[int(peak / step)]) - base)   # back to a quarter of the peak
    end = min(dur, start + max_len)
    calm = 0
    for i in range(int((peak + 5) / step), n):
        calm = calm + 1 if curve[i] <= calm_level else 0
        if calm * step >= 3:
            end = min(end, (i + 1) * step)
            break
    ends = [e for e in _sentence_ends(words) if end - 3 <= e <= end + 4]
    if ends:
        end = min(ends, key=lambda e: abs(e - end)) + 0.4
    return _fit(start, end, peak, dur, min_len, max_len)


def _fit(start, end, peak, dur, min_len, max_len):
    start, end = max(0.0, float(start)), min(dur, float(end))
    if end - start > max_len:          # too long: keep the peak, trim the far side
        if peak - start > max_len * 0.7:
            start = end - max_len
        else:
            end = start + max_len
    if end - start < min_len:          # too short: grow around it
        grow = min_len - (end - start)
        start = max(0.0, start - grow * 0.4)
        end = min(dur, start + min_len)
        start = max(0.0, end - min_len)
    return round(start, 2), round(end, 2), round(float(peak), 2)


def timeline(video, chat_rel, step=3.0):
    from spikes import kind
    db = virality.loudness(video, step)
    z = np.clip(virality._z(db), -3, 9)
    n = len(db)
    chat, laugh, clip = np.zeros(n, int), np.zeros(n, int), np.zeros(n, int)
    for t, body in chat_rel or []:
        i = int(t / step)
        if 0 <= i < n:
            chat[i] += 1
            k = kind(body)
            laugh[i] += k == "lol"
            clip[i] += k == "clip"
    return "\n".join(f"{i * step:.0f},{z[i]:.0f},{chat[i]},{laugh[i]},{clip[i]}" for i in range(n))


def _transcript(words, limit=3500):
    lines, cur, t0 = [], [], None
    for s, e, w in words:
        if t0 is None:
            t0 = s
        cur.append(w.strip())
        if w.strip().endswith((".", "!", "?")) or len(cur) >= 14:
            lines.append(f"[{t0:.0f}-{e:.0f}] {' '.join(cur)}"); cur, t0 = [], None
    if cur:
        lines.append(f"[{t0:.0f}-{words[-1][1]:.0f}] {' '.join(cur)}")
    return "\n".join(lines)[:limit] or "(no speech)"


def _chat_sample(chat_rel, guess, n=25):
    """A few chat lines, mostly from just after the moment, de-duplicated."""
    near = sorted(chat_rel or [], key=lambda x: abs(x[0] - (guess + 6)))
    seen, out = set(), []
    for t, body in near:
        b = ai.short(body, 40)
        if b.lower() in seen:
            continue
        seen.add(b.lower())
        out.append((t, b))
        if len(out) >= n:
            break
    return "\n".join(f"[{t:.0f}] {b}" for t, b in sorted(out)) or "(no chat)"


def layout_options(video, info, cfg):
    """Layouts Claude may choose between for this clip, or None when there's
    no real choice (no facecam found, or a layout is forced in config)."""
    if not ai.settings(cfg)["layout"] or info.get("forced_layout"):
        return None
    try:
        import layout
        lay = layout.find_layout(video)
    except Exception as e:
        print(f"  (couldn't look at the layout before judging: {e})")
        return None
    if lay["mode"] != "split" or not lay.get("cam"):
        return None
    # "game" isn't offered: with a camera found, a forced "game" becomes pip anyway
    allowed = [m for m in (cfg.get("layout", {}) or {}).get("allowed", list(LAYOUTS)) if m in LAYOUTS and m != "game"]
    if len(allowed) < 2:
        return None
    cam_w = lay["cam"][2] * 1920 / lay["size"][0]
    return {"modes": allowed, "cam_w": cam_w}


def ask_claude(video, words, chat_rel, clip_ranges, guess, min_len, max_len, cfg, info):
    """One small text-only call: the cut, the title/hook/comment and the layout."""
    if not ai.available(cfg, "edit"):
        return None
    info = info or {}
    streamer = info.get("streamer", "")
    dur = float(len(virality.loudness(video, 1.0)))
    opts = layout_options(video, info, cfg)
    lay_txt = lay_key = ""
    if opts:
        lay_txt = (f"\nThe stream has a facecam ({opts['cam_w']:.0f}px wide at 1080p; under 300 looks blurry blown up).\n"
                   "4. Pick the Short's \"layout\": " +
                   "; ".join(f"{m} = {LAYOUTS[m]}" for m in opts["modes"]) + ".\n")
        lay_key = ', "layout": "' + "|".join(opts["modes"]) + '"'
    recent = info.get("recent_titles") or []
    rec_txt = ("\nRecent titles on the channel (don't repeat these):\n<recent>\n" +
               "\n".join(ai.short(t, 90) for t in recent) + "\n</recent>\n") if recent else ""
    ct = info.get("clip_title") or ""
    prompt = PROMPT.format(
        streamer=streamer, channel=info.get("channel", ""), dur=dur, step=3.0,
        clips=("Viewers made Twitch clips of: " + ", ".join(f"{a:.0f}s to {b:.0f}s" for a, b in clip_ranges) + "\n"
               if clip_ranges else ""),
        clip_title=f"A viewer titled their clip: <clip_title>{ai.short(ct, 100)}</clip_title>\n" if ct else "",
        transcript=_transcript(words), timeline=timeline(video, chat_rel), chat=_chat_sample(chat_rel, guess),
        recent=rec_txt, layout=lay_txt, layout_key=lay_key, min_len=min_len, max_len=max_len)

    def check(ans):
        start, end = float(ans["start"]), float(ans["end"])
        if not (0 <= start < end <= dur + 1):
            raise ValueError(f"times out of range: {start}-{end}")
        if ans.get("viral_score") is not None and not 0 <= float(ans["viral_score"]) <= 10:
            raise ValueError(f"viral_score out of range: {ans['viral_score']}")

    ans = ai.ask("edit", prompt, cfg, check=check)
    if not ans:
        return None
    start, end = float(ans["start"]), float(ans["end"])
    peak = float(ans.get("peak", (start + end) / 2))
    s, e, p = _fit(start, end, peak, dur, min_len, max_len)
    score = float(ans["viral_score"]) if ans.get("viral_score") is not None else None
    out = {"start": s, "end": e, "peak": p, "summary": ai.short(ans.get("what_happened", ""), 300),
           "score": score, "why_score": ai.short(ans.get("why_score", ""), 120),
           "how": f"Claude (viral {score if score is not None else '?'}/10, "
                  f"confidence {ans.get('confidence', '?')})"}
    meta = {k: ai.short(ans.get(k), 120) for k in ("title", "hook", "about", "first_comment") if ans.get(k)}
    if meta:
        out["meta"] = meta
    if opts and ans.get("layout") in opts["modes"]:
        out["layout"] = ans["layout"]
    return out


def judge(video, guess, cfg, chat_rel=None, clip_ranges=(), streamer="", workdir=".", info=None):
    """Where the Short should start and end in `video`.

    guess       - where we think the moment is (seconds into `video`)
    chat_rel    - [(seconds into `video`, text)], as typed (not shifted)
    clip_ranges - [(start, end)] of viewers' Twitch clips, seconds into `video`
    info        - for Claude: streamer, channel, clip_title, recent_titles, forced_layout
    Returns {start, end, peak, summary, score, how}, plus "meta" and "layout"
    when Claude wrote the title and chose the layout. `score` is Claude's
    viral score (1-10, None from the built-in judge). Raises TooWeak when
    Claude rates it below quality.min_score."""
    sc = cfg.get("smart", {}) or {}
    min_len, max_len = float(sc.get("min_len", 20)), float(sc.get("max_len", 60))
    import editor
    words, _ = editor.transcribe(video, cfg.get("whisper_model", "small"))
    if sc.get("ai", True):
        info = dict(info or {}, streamer=(info or {}).get("streamer") or streamer)
        ans = ask_claude(video, words, chat_rel, list(clip_ranges), guess, min_len, max_len, cfg, info)
        if ans:
            q = quality(cfg)
            if q["enabled"] and ans.get("score") is not None and ans["score"] < float(q["min_score"]):
                raise TooWeak(ans["score"], ans.get("summary", ""), ans.get("why_score", ""))
            return ans
    step = 0.5
    curve, _ = virality.excitement(video, chat_rel, 0.0, cfg.get("chat_delay_s", 6), step)
    s, e, p = builtin(curve, step, words, guess, list(clip_ranges), min_len, max_len,
                      setup=float(sc.get("setup_s", 18)))
    return {"start": s, "end": e, "peak": p, "summary": "", "score": None, "how": "built-in judge"}
