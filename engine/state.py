"""state.json: which moments were posted, so nothing is posted twice.

    {"used": {vod_id: [seconds, ...]}, "used_clips": [clip ids],
     "posted": [{streamer, moment, t, video_id, title, ...}],
     "rejected": [{streamer, moment, t, score, day}]}   # Claude rated too weak

Several workflows write it (the scheduled Shorts and the live watcher), so
merge() combines two copies instead of letting the last push win.
"""
import json
import os

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(HERE, "state.json")


def load(path=PATH):
    if os.path.exists(path):
        with open(path) as f:
            st = json.load(f)
    else:
        st = {}
    st.setdefault("turn", 0)
    st.setdefault("used", {})
    st.setdefault("used_clips", [])
    st.setdefault("posted", [])
    st.setdefault("rejected", [])
    return st


def save(state, path=PATH):
    with open(path, "w") as f:
        json.dump(state, f, indent=2)


def is_used(state, key, t, gap=90):
    return any(abs(t - u) < gap for u in state["used"].get(key, []))


def norm_title(title):
    return " ".join("".join(ch for ch in (title or "").lower() if ch.isalnum() or ch == " ").split())


def was_rejected(state, m, gap=90):
    return any(r.get("moment") == m.key and abs(float(r.get("t", -1e9)) - m.t) < gap
               for r in state.get("rejected", []))


def mark_rejected(state, streamer, m, score, why=""):
    """Claude rated this moment too weak: never judge it again (saves calls)."""
    from datetime import datetime, timedelta, timezone
    now = datetime.now(timezone.utc)
    keep = (now - timedelta(days=21)).date().isoformat()
    state["rejected"] = [r for r in state.get("rejected", []) if r.get("day", "") >= keep]
    state["rejected"].append({"streamer": streamer, "moment": m.key, "t": m.t, "score": score,
                              "why": why, "clip_title": m.title, "day": now.date().isoformat()})


def is_repeat(state, streamer, m):
    """Was this moment posted already (or rated too weak)? Same part of the
    stream, any of the same clips, or a clip with the same title (deleted
    Shorts don't count)."""
    if was_rejected(state, m):
        return True
    if m.vod and is_used(state, m.vod["id"], m.t):
        return True
    if not m.vod and is_used(state, m.key, m.t):
        return True
    if set(m.clip_ids) & set(state.get("used_clips", [])):
        return True
    t = norm_title(m.title)
    return bool(t) and any(norm_title(p.get("clip_title")) == t for p in state["posted"]
                           if p["streamer"] == streamer and p.get("video_id") and not p.get("deleted"))


def mark_posted(state, streamer, m, video_id, meta, slot=None, quota_day=None, extra=None):
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    state["used"].setdefault(m.key, []).append(m.t)
    if m.vod and m.key != m.vod["id"]:
        state["used"].setdefault(m.vod["id"], []).append(m.t)
    state["used_clips"].extend(m.clip_ids)
    entry = {"streamer": streamer, "moment": m.key, "t": m.t, "score": m.views or m.viral,
             "viral": m.viral, "source": m.source,
             "video_id": video_id, "title": meta["title"],
             "clip_title": m.title, "clip_ids": m.clip_ids,
             "day": now.date().isoformat(), "at": now.isoformat(timespec="seconds"),
             "slot": slot.isoformat() if slot else None, "quota_day": quota_day}
    entry.update(extra or {})
    state["posted"].append(entry)


def _pkey(p):
    return p.get("video_id") or f"{p.get('streamer')}|{p.get('at') or p.get('day')}|{p.get('moment')}"


def merge(mine, theirs):
    """Union of two state copies. `theirs` (what's on GitHub now) wins on
    conflicting fields, e.g. a Short someone marked as deleted."""
    out = dict(mine)
    out.update({k: v for k, v in theirs.items() if k not in ("used", "used_clips", "posted", "turn", "rejected")})
    rej = list(theirs.get("rejected", []))
    seen = {(r.get("moment"), r.get("t")) for r in rej}
    rej += [r for r in mine.get("rejected", []) if (r.get("moment"), r.get("t")) not in seen]
    out["rejected"] = rej
    out["turn"] = max(mine.get("turn", 0), theirs.get("turn", 0))
    used = {}
    for src in (theirs.get("used", {}), mine.get("used", {})):
        for k, ts in src.items():
            cur = used.setdefault(k, [])
            cur.extend(t for t in ts if t not in cur)
    out["used"] = used
    clips = list(theirs.get("used_clips", []))
    clips += [c for c in mine.get("used_clips", []) if c not in clips]
    out["used_clips"] = clips
    posted, index = [], {}
    for p in theirs.get("posted", []):
        index[_pkey(p)] = len(posted)
        posted.append(dict(p))
    for p in mine.get("posted", []):
        k = _pkey(p)
        if k in index:
            posted[index[k]] = {**p, **posted[index[k]]}
        else:
            index[k] = len(posted)
            posted.append(dict(p))
    out["posted"] = posted
    return out
