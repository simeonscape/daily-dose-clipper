"""Write the hook, title, description, hashtags and first comment.

No API key needed. Picked automatically:

1. Claude (optional, see engine/ai.py). The smart judge already asks Claude
   for the title, hook and first comment in the same call that picks the cut,
   so normally there's no extra call here. Only if that didn't happen (clip
   with no VOD, smart judge off) one small title call is made.
2. Built-in writer (always there). Reads what chat was spamming to tell what
   kind of moment it was (funny, hype, shocking, a fail, rage), pulls a punchy
   line from the transcript, and fills Shorts patterns. With Claude set up,
   the patterns get a fresh batch from Claude once a week ("smart templates",
   one call a week), so the built-in titles don't go stale either.

Either way, a title or hook that was posted in the last few weeks is never
used again.
"""
import hashlib
import random
import re

import ai

MOODS = {
    "funny": r"KEKW|OMEGALUL|LULW?|LMA+O+|LMFAO|ICANT|HAHA+|😂|🤣|💀|DEAD|CRYING",
    "hype": r"\bW+\b|POG\w*|LETS+ GO+|GOAT|HUGE|INSANE|🔥|CLIP",
    "shock": r"WTF|HOLY|NAH+|NO WAY|WHAT+|monka\w*|😳|😱|OMG|BRO+",
    "fail": r"\bL+\b|RIP|F+\b|RATIO|SKILL ISSUE|BOZO|DIFF",
    "rage": r"MAD|RAGE|TILT|CALM DOWN|😡|🤬|SCREAM",
}

HOOKS = {
    "funny": ["HE CAN'T STOP LAUGHING", "CHAT LOST IT AT THIS", "WAIT FOR THE ENDING",
              "THIS GOT EVERYONE", "I CAN'T BREATHE", "NOBODY WAS READY"],
    "hype": ["THIS IS INSANE", "CHAT WENT CRAZY", "HE ACTUALLY DID IT", "WATCH THIS",
             "THE WHOLE STREAM ERUPTED", "BIGGEST MOMENT OF THE STREAM"],
    "shock": ["DID THAT JUST HAPPEN?", "NO WAY THIS IS REAL", "WAIT FOR IT",
              "NOBODY SAW THIS COMING", "CHAT WAS SPEECHLESS", "HE DIDN'T EXPECT THIS"],
    "fail": ["THIS WENT SO WRONG", "HE WILL NEVER LIVE THIS DOWN", "WORST TIMING EVER",
             "CHAT DESTROYED HIM", "IT ALL FELL APART", "WAIT FOR IT"],
    "rage": ["HE COMPLETELY LOST IT", "DON'T MAKE HIM MAD", "THE RAGE IS REAL",
             "HE WAS NOT HAPPY", "CHAT PUSHED HIM TOO FAR"],
}

TITLES = {
    "funny": ["{s} couldn't stop laughing at this 😂", "{s} had chat in tears 💀",
              "This {s} moment is too funny", "{s} said WHAT? 😂", "\"{q}\" 💀 ({s})"],
    "hype": ["{s} actually pulled this off 🔥", "Chat went crazy for {s} 🔥",
             "{s}'s biggest moment of the stream", "{s} is different 🐐", "\"{q}\" 🔥 ({s})"],
    "shock": ["{s} did NOT expect this 😳", "Nobody saw this coming ({s})",
              "{s}'s chat couldn't believe it", "Wait for {s}'s reaction 😱", "\"{q}\" 😳 ({s})"],
    "fail": ["{s} will never live this down 💀", "This went so wrong for {s}",
             "{s}'s worst moment on stream", "Chat roasted {s} for this", "\"{q}\" 💀 ({s})"],
    "rage": ["{s} completely lost it 😡", "Chat pushed {s} too far", "{s} was NOT happy",
             "Don't make {s} mad 😤", "\"{q}\" 😡 ({s})"],
}

LINES = {
    "funny": "Chat could not keep it together after this one.",
    "hype": "The whole stream erupted for this one.",
    "shock": "Nobody in chat saw this coming.",
    "fail": "Chat is never letting this one go.",
    "rage": "This one pushed {s} over the edge.",
}

COMMENTS = {
    "funny": ["Rate this from 1 to 10 😂", "Who else replayed this 5 times? 💀", "Tag someone who laughs like this 😂"],
    "hype": ["W or L? 👇", "Is this the best moment of the stream? 🔥", "Could you have done this? 👇"],
    "shock": ["Did you see that coming? 😳", "What would you have done here? 👇", "Real or staged? 👇"],
    "fail": ["Skill issue or bad luck? 💀", "W or L? 👇", "Has this ever happened to you? 💀"],
    "rage": ["Was he right to be mad? 👇", "Who's side are you on? 👇", "Would you have reacted the same? 😤"],
}

TAGS = {
    "funny": ["#funny", "#streamerclips"], "hype": ["#viral", "#streamerclips"],
    "shock": ["#viral", "#twitchclips"], "fail": ["#fail", "#twitchclips"],
    "rage": ["#rage", "#streamerclips"],
}


def mood_of(chat_lines):
    counts = {m: 0 for m in MOODS}
    for line in chat_lines:
        for m, pat in MOODS.items():
            if re.search(pat, line, re.IGNORECASE):
                counts[m] += 1
    best = max(counts, key=counts.get)
    return best if counts[best] else "shock"


def best_quote(transcript):
    """A short, punchy sentence from the clip (3 to 8 words), or None."""
    sents = [s.strip(" .,-") for s in re.split(r"(?<=[.!?])\s+", transcript or "") if s.strip()]
    good = [s for s in sents if 3 <= len(s.split()) <= 8 and len(s) <= 45]
    if not good:
        return None
    good.sort(key=lambda s: (s.endswith("!") or s.endswith("?"), -abs(len(s.split()) - 5)), reverse=True)
    return good[0].rstrip(".")


EMOJI = {"funny": "😂", "hype": "🔥", "shock": "😳", "fail": "💀", "rage": "😡"}


def usable_clip_title(title, streamer):
    """A clip title written by a viewer, if it says something about the moment.
    Twitch fills in the stream's own title when the clipper doesn't write one,
    and those are usually long and full of !commands, so they're skipped."""
    t = (title or "").strip()
    if not (4 <= len(t) <= 70) or "!" in t[:1] or re.search(r"![a-z]|\||#\w|https?://", t, re.I):
        return None
    if t.lower().replace(" ", "") in (streamer.lower().replace(" ", ""), "clip", "lol", "kekw"):
        return None
    if len(re.findall(r"[A-Za-z]{2,}", t)) < 2:
        return None
    return t


def _norm(t):
    return " ".join("".join(ch for ch in (t or "").lower() if ch.isalnum() or ch == " ").split())


def _pool(kind, mood):
    """Built-in patterns plus the ones Claude wrote this week."""
    base = {"hooks": HOOKS, "titles": TITLES, "comments": COMMENTS}[kind][mood]
    extra = ((ai.load().get("templates") or {}).get("moods") or {}).get(mood, {}).get(kind, [])
    return list(dict.fromkeys(list(base) + [x for x in extra if isinstance(x, str)]))


def _fresh(options, used, rng):
    """A random option that wasn't used lately (or any, if all were)."""
    used = {_norm(u) for u in used}
    good = [o for o in options if _norm(o) not in used]
    return rng.choice(good or options)


def builtin(streamer, stream_url, transcript, chat_lines, seed="", clip_title=None, recent=(), recent_hooks=()):
    rng = random.Random(hashlib.md5((streamer + seed).encode()).hexdigest())
    mood = mood_of(chat_lines)
    tag = "".join(ch for ch in streamer if ch.isalnum()).lower()
    quote = best_quote(transcript)
    ct = usable_clip_title(clip_title, streamer)
    title = None
    if ct:
        ct = ct[0].upper() + ct[1:]
        title = ct if streamer.lower() in ct.lower() else f"{ct} ({streamer})"
        if not re.search(r"[^\w\s.,!?'\"()-]", title):
            title += " " + EMOJI[mood]
        title = title[:95]
        if _norm(title) in {_norm(r) for r in recent}:
            title = None
    if not title:
        titles = []
        for t in _pool("titles", mood):
            if "{q}" in t and not quote:
                continue
            try:
                titles.append(t.format(s=streamer, q=quote)[:95])
            except (KeyError, IndexError, ValueError):
                continue
        title = _fresh(titles, recent, rng)
    hook = (ct.upper() if ct and len(ct.split()) <= 7 and len(ct) <= 42
            and _norm(ct) not in {_norm(h) for h in recent_hooks}
            else _fresh(_pool("hooks", mood), recent_hooks, rng))
    return {
        "hook": hook,
        "title": title,
        "description": _description(LINES[mood].format(s=streamer), streamer, stream_url),
        "hashtags": ["#shorts", f"#{tag}"] + TAGS[mood] + ["#dailydoseofstreamers"],
        "first_comment": rng.choice(_pool("comments", mood)),
    }


def _description(line, streamer, stream_url):
    return (f"{line}\n\n"
            f"Clip from {streamer}'s stream: {stream_url}\n"
            f"Fan channel. Not affiliated with {streamer}. All rights belong to the creator.")


def with_ai(base, got, streamer, stream_url, recent=(), recent_hooks=()):
    """Claude's title/hook/about/first comment on top of the built-in metadata.
    Anything missing, too long or recently used keeps the built-in version."""
    meta = dict(base)
    used = {_norm(r) for r in recent}
    t = (got.get("title") or "").strip().strip('"')
    if 8 <= len(t) <= 95 and _norm(t) not in used:
        meta["title"] = t
    elif t:
        print(f"  (Claude's title \"{t}\" was too long or used recently; using the built-in one)")
    h = (got.get("hook") or "").strip().strip('"').upper()
    if h and len(h.split()) <= 7 and len(h) <= 42 and _norm(h) not in {_norm(x) for x in recent_hooks}:
        meta["hook"] = h
    if got.get("about"):
        meta["description"] = _description(ai.short(got["about"], 200), streamer, stream_url)
    if got.get("first_comment"):
        meta["first_comment"] = ai.short(got["first_comment"], 150)
    meta["written_by"] = "claude"
    return meta


PROMPT = """Write the text for a YouTube Short on the clip channel "{channel}", from {streamer}'s stream.
{clip_title}What happened: <summary>{summary}</summary>
Transcript: <transcript>{transcript}</transcript>
Chat: <chat>{chat}</chat>
{recent}
Reply: {{"title": "max 70 characters, includes {streamer}, curiosity-driven but true, not a copy of a recent title",
"hook": "on-screen text for the first 2 seconds, max 7 words, all caps, no emoji, no spoilers",
"about": "one short line about the moment", "first_comment": "a short question that gets replies"}}"""


def _recent_block(recent):
    return ("Recent titles on the channel (don't repeat):\n<recent>\n" + "\n".join(ai.short(t, 90) for t in recent)
            + "\n</recent>\n") if recent else ""


def via_claude(streamer, transcript, chat_lines, channel, clip_title, summary, recent, cfg):
    """The one small title call, only when the smart judge didn't write the title."""
    if not ai.available(cfg, "titles"):
        return None
    seen, chat = set(), []
    for c in chat_lines or []:
        c = ai.short(c, 40)
        if c.lower() not in seen:
            seen.add(c.lower()); chat.append(c)
    prompt = PROMPT.format(channel=channel, streamer=streamer,
                           clip_title=(f"A viewer titled the clip: <clip_title>{ai.short(clip_title, 100)}</clip_title>\n"
                                       if clip_title else ""),
                           summary=ai.short(summary, 300) or "(unknown)",
                           transcript=ai.short(transcript, 2000) or "(no speech)",
                           chat=" / ".join(chat[:30]) or "(none)", recent=_recent_block(recent))

    def check(ans):
        assert ans.get("title") and ans.get("hook"), "missing title or hook"

    return ai.ask("titles", prompt, cfg, check=check)


def write_metadata(streamer, platform, stream_url, transcript, chat_lines,
                   channel="Daily Dose of Streamers", seed="", clip_title=None, summary=None,
                   ai_meta=None, recent=(), recent_hooks=(), cfg=None):
    base = builtin(streamer, stream_url, transcript, chat_lines, seed=seed, clip_title=clip_title,
                   recent=recent, recent_hooks=recent_hooks)
    got = ai_meta or via_claude(streamer, transcript, chat_lines, channel, clip_title, summary, recent, cfg)
    if got:
        return with_ai(base, got, streamer, stream_url, recent, recent_hooks)
    return base


# ---- smart templates: a fresh batch of patterns once a week ---------------

TEMPLATE_PROMPT = """Write fresh YouTube Shorts text patterns for a channel that posts streamer clips
(CaseOh, Kai Cenat, Jynxzi, ohnepixel and others). For each mood: {moods}.
Use {{s}} where the streamer's name goes. Titles max 60 characters (an emoji at the end is fine),
hooks max 6 words in ALL CAPS with no emoji, comments are short questions that get replies.
Be varied, punchy and true-to-life for stream clips; don't copy these existing ones:
<existing>
{existing}
</existing>
Reply: {{"funny": {{"titles": [6], "hooks": [6], "comments": [3]}}, "hype": {{...}}, "shock": {{...}}, "fail": {{...}}, "rage": {{...}}}}"""


def _clean_templates(ans):
    moods = {}
    for mood in MOODS:
        m = ans.get(mood) or {}
        titles = [t.strip() for t in m.get("titles", []) if isinstance(t, str) and "{s}" in t
                  and len(t) <= 80 and re.fullmatch(r"[^{}]*(\{s\}[^{}]*)+", t)]
        hooks = [h.strip().upper() for h in m.get("hooks", []) if isinstance(h, str)
                 and "{" not in h and len(h.split()) <= 7 and len(h) <= 42]
        comments = [c.strip() for c in m.get("comments", []) if isinstance(c, str) and "{" not in c and len(c) <= 120]
        moods[mood] = {"titles": titles[:8], "hooks": hooks[:8], "comments": comments[:5]}
    if sum(len(v["titles"]) for v in moods.values()) < 5:
        raise ValueError("too few usable title patterns")
    return moods


def refresh_templates(cfg, state=None):
    """Once every `ai.templates_every_days` days: one call for new patterns."""
    a = ai.settings(cfg)
    st = ai.load()
    made = (st.get("templates") or {}).get("made", "")
    from datetime import date, timedelta
    if made and made > (date.today() - timedelta(days=int(a["templates_every_days"]))).isoformat():
        return False
    if not ai.available(cfg, "templates"):
        return False
    existing = []
    for mood in MOODS:
        existing += [t.replace("{q}", "...") for t in TITLES[mood][:2]] + HOOKS[mood][:1]
    existing += [ai.short(t, 80) for t in ai.recent_titles(state, n=10)]
    prompt = TEMPLATE_PROMPT.format(moods=", ".join(MOODS), existing="\n".join(existing))
    ans = ai.ask("templates", prompt, cfg, check=_clean_templates)
    if not ans:
        return False
    st["templates"] = {"made": date.today().isoformat(), "moods": _clean_templates(ans)}
    ai.save(st)
    print("New title/hook patterns from Claude for this week: "
          + ", ".join(f"{m} {len(v['titles'])}" for m, v in st["templates"]["moods"].items()))
    return True
