"""Find nudity in a clip and blur only those spots.

Games like GTA have strip clubs and nude characters, and YouTube age-restricts
or removes Shorts that show them. This scans the clip with NudeNet (a small
detector that runs on the CPU, no API key, the model ships inside the pip
package) and blurs just the boxes it finds, only while they're on screen.
The rest of the picture stays sharp.

How it works:
  1. Look at `fps` frames per second. Each frame is checked whole and in four
     overlapping tiles, because a character in a 1080p game is tiny once the
     whole frame is shrunk to the detector's 320 px input.
  2. Every hit becomes a padded box that stays blurred a little before and
     after the frame it was seen in (characters move between checked frames).
  3. A second pass blurs those boxes in every frame and keeps the sound.

If most of the clip is explicit, blurring won't save it: is_unsafe() tells the
engine to skip that moment and use the next one.

Settings in config.yaml under `safety:` (see there).
"""
import subprocess

import cv2
import numpy as np

EXPLICIT = {
    "FEMALE_BREAST_EXPOSED", "FEMALE_GENITALIA_EXPOSED", "MALE_GENITALIA_EXPOSED",
    "BUTTOCKS_EXPOSED", "ANUS_EXPOSED",
}
_detector = None


class Unsafe(Exception):
    """The clip is mostly explicit; post a different moment."""


def _det():
    global _detector
    if _detector is None:
        from nudenet import NudeDetector
        _detector = NudeDetector()
    return _detector


def _tiles(w, h):
    """Whole frame plus 2x2 overlapping tiles."""
    tw, th = int(w * 0.6), int(h * 0.6)
    out = [(0, 0, w, h)]
    for x in (0, w - tw):
        for y in (0, h - th):
            out.append((x, y, tw, th))
    return out


def detect_frame(img, classes, threshold, tiles=True):
    h, w = img.shape[:2]
    hits = []
    for x0, y0, tw, th in (_tiles(w, h) if tiles else [(0, 0, w, h)]):
        for d in _det().detect(np.ascontiguousarray(img[y0:y0 + th, x0:x0 + tw])):
            if d["class"] in classes and d["score"] >= threshold:
                x, y, bw, bh = d["box"]
                if bw * bh < 0.0004 * w * h:      # a few pixels: noise
                    continue
                hits.append((x + x0, y + y0, bw, bh, d["score"], d["class"]))
    return hits


def scan(video, cfg=None):
    """Explicit boxes in the clip: [(t, x, y, w, h, score, class)] in source pixels."""
    cfg = cfg or {}
    fps = float(cfg.get("fps", 2))
    classes = set(cfg.get("classes", EXPLICIT))
    thr = float(cfg.get("threshold", 0.45))
    cap = cv2.VideoCapture(video)
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    step = max(1, int(round(src_fps / fps)))
    out, i, checked = [], 0, 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        if i % step == 0:
            ok, img = cap.retrieve()
            if ok:
                checked += 1
                for x, y, w, h, sc, cl in detect_frame(img, classes, thr, cfg.get("tiles", True)):
                    out.append((i / src_fps, x, y, w, h, sc, cl))
        i += 1
    cap.release()
    return out, checked, (total or i) / src_fps


def regions(hits, size, fps=2.0, pad=0.3, hold=None):
    """Hits -> boxes to blur with the time they're active: [(t0, t1, x, y, w, h)]."""
    W, H = size
    hold = hold if hold is not None else 0.75 / fps
    out = []
    for t, x, y, w, h, *_ in hits:
        px, py = pad * w, pad * h
        x0, y0 = max(0, x - px), max(0, y - py)
        x1, y1 = min(W, x + w + px), min(H, y + h + py)
        out.append([max(0.0, t - hold), t + hold, x0, y0, x1, y1])
    return out


def _box_at(regs, t):
    return [(int(x0), int(y0), int(x1), int(y1)) for t0, t1, x0, y0, x1, y1 in regs if t0 <= t <= t1]


def _blur(img, x0, y0, x1, y1):
    roi = img[y0:y1, x0:x1]
    if roi.size == 0:
        return
    h, w = roi.shape[:2]
    # pixelate then blur: nothing readable survives, but it looks soft, not blocky
    small = cv2.resize(roi, (max(1, w // 16), max(1, h // 16)), interpolation=cv2.INTER_AREA)
    k = max(3, (min(w, h) // 6) | 1)
    img[y0:y1, x0:x1] = cv2.GaussianBlur(cv2.resize(small, (w, h), interpolation=cv2.INTER_LINEAR), (k, k), 0)


def apply(video, regs, out):
    """Blur `regs` in every frame of `video`, keep the audio."""
    cap = cv2.VideoCapture(video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    enc = subprocess.Popen(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}",
         "-r", f"{fps:.5f}", "-i", "-", "-i", video, "-map", "0:v", "-map", "1:a?",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-pix_fmt", "yuv420p", "-c:a", "copy",
         "-shortest", out], stdin=subprocess.PIPE)
    i = 0
    try:
        while True:
            ok, img = cap.read()
            if not ok:
                break
            for box in _box_at(regs, i / fps):
                _blur(img, *box)
            enc.stdin.write(img.tobytes())
            i += 1
    finally:
        cap.release()
        enc.stdin.close()
    if enc.wait() != 0:
        raise RuntimeError("ffmpeg failed while blurring")
    return out


def censor(video, cfg=None, debug_path=None):
    """Scan and blur. Returns (video to use, report). Raises Unsafe if the clip
    is explicit for more than `max_explicit_share` of its length."""
    cfg = cfg or {}
    if cfg.get("enabled", True) is False:
        return video, {"checked": 0}
    hits, checked, dur = scan(video, cfg)
    fps = float(cfg.get("fps", 2))
    times = sorted({round(h[0] * fps) for h in hits})
    share = len(times) / max(1, checked)
    report = {"checked": checked, "frames_flagged": len(times), "share": round(share, 3),
              "classes": sorted({h[6] for h in hits})}
    if not hits:
        return video, report
    print(f"  explicit content in {len(times)} of {checked} checked frames ({share:.0%}): "
          f"{', '.join(report['classes'])}")
    if share > float(cfg.get("max_explicit_share", 0.35)):
        raise Unsafe(f"{share:.0%} of the clip is explicit")
    cap = cv2.VideoCapture(video)
    size = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    cap.release()
    regs = regions(hits, size, fps=fps, pad=float(cfg.get("pad", 0.3)))
    out = video.replace(".mp4", "_safe.mp4")
    apply(video, regs, out)
    report["blurred_boxes"] = len(regs)
    if debug_path:
        _debug(video, hits, debug_path)
    return out, report


def _debug(video, hits, path):
    """A frame showing what it blurred (red boxes, already blurred), saved next to the Short."""
    t = max(hits, key=lambda h: h[5])[0]
    cap = cv2.VideoCapture(video)
    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000)
    ok, img = cap.read()
    cap.release()
    if not ok:
        return
    for ht, x, y, w, h, sc, cl in hits:
        if abs(ht - t) < 0.01:
            _blur(img, int(x), int(y), int(x + w), int(y + h))
            cv2.rectangle(img, (int(x), int(y)), (int(x + w), int(y + h)), (0, 0, 255), 3)
            cv2.putText(img, f"{cl} {sc:.2f}", (int(x), int(y) - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
    small = cv2.resize(img, (img.shape[1] // 2, img.shape[0] // 2))
    cv2.imwrite(path, small)
