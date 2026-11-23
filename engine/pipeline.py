"""From a downloaded piece of stream to a finished, branded Short.

    cut the best window -> blur explicit spots -> subtitles -> title/hook ->
    pick the layout and follow the action -> render

Used by run.py (scheduled Shorts from VODs and clips) and live.py (live streams).
"""
import json
import os
import subprocess

import editor
import framing
import layout
import metadata
import safety
import virality


def cut(src, start, end, out):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.2f}", "-i", src, "-t", f"{end - start:.2f}",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-c:a", "aac", "-b:a", "192k", out],
                   check=True)
    return out


def best_window(seg, m_t, cfg, chat=None, seg_start=0.0):
    """Cut `seg` down to the Short around its most exciting point.
    `m_t` is where the moment should be, in seconds into `seg`."""
    before, after = cfg["before_s"], cfg["after_s"]
    vc = cfg.get("virality", {}) or {}
    if vc.get("refine", True):
        try:
            a, b, peak = virality.refine(seg, m_t, before, after, chat=chat, seg_start=seg_start,
                                         chat_delay=cfg.get("chat_delay_s", 6),
                                         search_s=vc.get("search_s", 20))
            print(f"  best window {a:.1f}-{b:.1f}s of the download (peak at {peak:.1f}s, guess was {m_t:.1f}s)")
        except Exception as e:
            print(f"  (couldn't refine the window: {e})")
            a, b = max(0.0, m_t - before), m_t + after
    else:
        a, b = max(0.0, m_t - before), m_t + after
    return cut(seg, a, b, seg.replace(".mp4", "_cut.mp4")), a


def make_short(s, seg, msgs, cfg, tag, outdir, workdir, clip_title=""):
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
                                   channel=cfg["channel_name"], seed=tag, clip_title=clip_title)

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
