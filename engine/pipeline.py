"""From a downloaded piece of stream to a finished, branded Short.

    cut the best window -> blur explicit spots -> subtitles -> title/hook ->
    pick the layout and follow the action -> render

Used by run.py (scheduled Shorts from VODs and clips) and live moments the watcher signals (run.py --live).
"""
import json
import os
import subprocess

import editor
import framing
import judge
import layout
import metadata
import safety
import virality


def cut(src, start, end, out):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.2f}", "-i", src, "-t", f"{end - start:.2f}",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-c:a", "aac", "-b:a", "192k", out],
                   check=True)
    return out


def best_window(seg, m_t, cfg, chat=None, seg_start=0.0, clip_ranges=(), streamer="", workdir="."):
    """Cut `seg` down to the Short. Returns (cut video, start in `seg`, summary).

    `m_t` is where we think the moment is (seconds into `seg`), `chat` is
    [(seconds into the VOD, text)], `seg_start` where `seg` starts in the VOD,
    `clip_ranges` [(start, end)] of viewers' clips in the VOD.
    With `smart:` on, judge.py studies the whole download and picks the cut;
    otherwise the old fixed window around the loudest point is used."""
    sc = cfg.get("smart", {}) or {}
    if sc.get("enabled", True):
        try:
            norm, skip = editor.normalize(seg, return_start=True)
            off = seg_start + skip            # where `norm` starts in the VOD
            rel_chat = [(t - off, b) for t, b in (chat or [])]
            rel_clips = [(a - off, b - off) for a, b in clip_ranges]
            j = judge.judge(norm, m_t - skip, cfg, chat_rel=rel_chat, clip_ranges=rel_clips,
                            streamer=streamer, workdir=workdir)
            print(f"  {j['how']}: Short is {j['start']:.1f}-{j['end']:.1f}s of the download "
                  f"(peak {j['peak']:.1f}s, {j['end'] - j['start']:.0f}s long)"
                  + (f"\n  what happened: {j['summary']}" if j["summary"] else ""))
            return cut(norm, j["start"], j["end"], seg.replace(".mp4", "_cut.mp4")), j["start"] + skip, j["summary"]
        except Exception as e:
            print(f"  (smart judge failed: {e}; using the fixed window)")
    before, after = cfg["before_s"], cfg["after_s"]
    vc = cfg.get("virality", {}) or {}
    a, b = max(0.0, m_t - before), m_t + after
    if vc.get("refine", True):
        try:
            a, b, peak = virality.refine(seg, m_t, before, after, chat=chat, seg_start=seg_start,
                                         chat_delay=cfg.get("chat_delay_s", 6),
                                         search_s=vc.get("search_s", 20))
            print(f"  best window {a:.1f}-{b:.1f}s of the download (peak at {peak:.1f}s, guess was {m_t:.1f}s)")
        except Exception as e:
            print(f"  (couldn't refine the window: {e})")
    return cut(seg, a, b, seg.replace(".mp4", "_cut.mp4")), a, ""


def make_short(s, seg, msgs, cfg, tag, outdir, workdir, clip_title="", summary=""):
    """Render the Short for streamer `s` from `seg` (already cut to length).
    Returns (video path, metadata dict)."""
    seg = editor.normalize(seg)
    os.makedirs(outdir, exist_ok=True)
    out = os.path.join(outdir, f"{tag}.mp4")

    seg, safety_report = safety.censor(seg, cfg.get("safety", {}) or {},
                                       debug_path=out.replace(".mp4", "_blurred.jpg"))

    words, text = editor.transcribe(seg, cfg.get("whisper_model", "small"))
    stream_url = f"https://twitch.tv/{s['login']}"
    meta = metadata.write_metadata(s["name"], "Twitch", stream_url, text, msgs,
                                   channel=cfg["channel_name"], seed=tag, clip_title=clip_title,
                                   summary=summary)

    lay = layout.find_layout(seg, override=s.get("cam"), debug_path=out.replace(".mp4", "_camera.jpg"))
    lcfg = dict(cfg.get("layout", {}) or {})
    override = s.get("layout") or lcfg.get("force")
    if override == "game" and lay["mode"] == "split":
        override = "pip"            # old setting: keep the game big, still show the cam
    if override == "cam" and lay["mode"] == "split":
        lay = dict(lay, mode="cam")
    for k in ("follow", "game_zoom", "min_cam_width"):
        if k in s:
            lcfg[k] = s[k]
    p = framing.plan(seg, lay, lcfg, override=override)
    print(f"  layout: {p['mode']} ({p['why']})")
    editor.render(seg, out, meta["hook"], s["name"], words, p, login=s["login"], workdir=workdir)
    meta["layout"] = p["mode"]
    meta["safety"] = safety_report
    with open(out.replace(".mp4", ".json"), "w") as f:
        json.dump(meta, f, indent=2, ensure_ascii=False)
    print(f"  rendered {os.path.basename(out)}\n  title: {meta['title']}\n  hook:  {meta['hook']}")
    return out, meta
