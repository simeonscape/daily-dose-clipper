"""Pick the Short's layout for this clip and follow the important part of the screen.

Layouts (drawn by editor.py):
  split - facecam on top, the whole screen below. The default when the stream
          has a decent facecam.
  pip   - the gameplay fills one tall panel, cropped to follow the action,
          with the facecam as a small box in the corner. Used when the facecam
          is too small to blow up without looking blurry, or the moment is
          all about the game (the camera barely moves).
  cam   - one tall panel of the camera (Just Chatting / IRL), following the
          streamer's face as they move around.
  game  - no camera: one panel of the stream, following the action.

Following the action: frames are sampled 4 times a second. In each, the spots
that changed most since the last sample (things moving, explosions, a car)
and any faces pull the crop toward them. The path is smoothed and its speed
capped, so the crop glides instead of jumping around, and it sits still
when nothing in particular is happening.
"""
import cv2
import numpy as np

import layout as lay_mod

AW = 320                     # analysis width


def _samples(video, fps=4.0):
    cap = cv2.VideoCapture(video)
    src = cap.get(cv2.CAP_PROP_FPS) or 30
    step = max(1, int(round(src / fps)))
    out, i = [], 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        if i % step == 0:
            ok, im = cap.retrieve()
            if ok:
                out.append((i / src, im))
        i += 1
    cap.release()
    return out


def _mask(size, exclude):
    W, H = size
    s = AW / W
    m = np.ones((int(H * s), AW), np.float32)
    if exclude:
        x, y, w, h = exclude
        m[int(y * s):int((y + h) * s) + 1, int(x * s):int((x + w) * s) + 1] = 0
    return m, s


def activity(samples, size, exclude=None):
    """Per sample: (t, cx, cy, strength, spread) of what's moving, in source px."""
    if not samples:
        return []
    mask, s = _mask(size, exclude)
    prev, out = None, []
    for t, im in samples:
        g = cv2.GaussianBlur(cv2.cvtColor(cv2.resize(im, (mask.shape[1], mask.shape[0])), cv2.COLOR_BGR2GRAY),
                             (5, 5), 0).astype(np.float32)
        if prev is None:
            prev = g
            continue
        d = np.abs(g - prev) * mask
        prev = g
        thr = np.percentile(d[mask > 0], 92) if (mask > 0).any() else 0
        hot = np.where(d > max(thr, 8), d, 0)
        tot = float(hot.sum())
        if tot <= 0:
            out.append((t, None, None, 0.0, 1.0))
            continue
        ys, xs = np.nonzero(hot)
        wts = hot[ys, xs]
        cx, cy = float((xs * wts).sum() / tot), float((ys * wts).sum() / tot)
        spread = float(np.sqrt((((xs - cx) ** 2) * wts).sum() / tot)) / mask.shape[1]
        out.append((t, cx / s, cy / s, float(d.mean()), spread))
    return out


def faces(samples, size, exclude=None):
    """Per sample: biggest face centre (source px) or None."""
    W, H = size
    det = cv2.FaceDetectorYN.create(lay_mod.MODEL, "", (W, H), 0.6, 0.3, 20)
    out = []
    for t, im in samples[::2]:            # 2 per second is plenty
        det.setInputSize((im.shape[1], im.shape[0]))
        _, f = det.detect(im)
        best = None
        if f is not None:
            for x, y, w, h in (r[:4] for r in f):
                cx, cy = x + w / 2, y + h / 2
                if exclude:
                    ex, ey, ew, eh = exclude
                    if ex <= cx <= ex + ew and ey <= cy <= ey + eh:
                        continue
                if best is None or w > best[2]:
                    best = (cx, cy, w)
        out.append((t, best))
    return out


def _smooth_path(ts, xs, lo, hi, max_speed, dead):
    """Smooth target positions into a calm camera path within [lo, hi]."""
    xs = np.asarray(xs, np.float32)
    if len(xs) == 0:
        return xs
    k = 5
    if len(xs) >= k:   # rolling median kills one-off jumps
        pad = np.pad(xs, k // 2, mode="edge")
        xs = np.array([np.median(pad[i:i + k]) for i in range(len(xs))], np.float32)
    path = [float(np.clip(xs[0], lo, hi))]
    for i in range(1, len(xs)):
        dt = max(1e-3, ts[i] - ts[i - 1])
        cur, tgt = path[-1], float(np.clip(xs[i], lo, hi))
        if abs(tgt - cur) < dead:          # small moves: hold still
            path.append(cur)
            continue
        step = np.clip((tgt - cur) * 0.35, -max_speed * dt, max_speed * dt)
        path.append(cur + float(step))
    # one more pass of easing
    p = np.asarray(path, np.float32)
    if len(p) >= 5:
        p = np.convolve(np.pad(p, 2, mode="edge"), np.ones(5) / 5, mode="valid")
    return np.clip(p, lo, hi)


def track(samples, size, crop_w, crop_h, region=None, exclude=None, follow="action"):
    """Keyframes [(t, x, y)] (top-left of a crop_w x crop_h box) following the
    action or a face, inside `region` (x, y, w, h; default the whole frame)."""
    W, H = size
    rx, ry, rw, rh = region or (0, 0, W, H)
    crop_w, crop_h = min(crop_w, rw), min(crop_h, rh)
    cx0, cy0 = rx + rw / 2, ry + rh / 2
    ts = [t for t, _ in samples]
    tx = np.full(len(ts), cx0, np.float32)
    ty = np.full(len(ts), cy0, np.float32)
    if follow == "face":
        fc = dict((round(t, 3), f) for t, f in faces(samples, size))
        last = (cx0, cy0)
        for i, t in enumerate(ts):
            f = fc.get(round(t, 3))
            if f:
                last = (f[0], f[1] + 0.15 * f[2])   # a bit below the eyes keeps the head comfy
            tx[i], ty[i] = last
    else:
        act = {round(a[0], 3): a for a in activity(samples, size, exclude)}
        fc = dict((round(t, 3), f) for t, f in faces(samples, size, exclude))
        for i, t in enumerate(ts):
            a = act.get(round(t, 3))
            x, y, wsum = cx0, cy0, 0.6          # a pull toward the middle
            if a and a[1] is not None:
                focus = max(0.0, 1.0 - a[4] * 3)    # spread-out motion (camera pans) -> trust it less
                w = 1.5 * focus
                x, y, wsum = (x * wsum + a[1] * w), (y * wsum + a[2] * w), wsum + w
                x, y = x / wsum, y / wsum
            f = fc.get(round(t, 3))
            if f:
                x, y = 0.5 * x + 0.5 * f[0], 0.5 * y + 0.5 * f[1]
            tx[i], ty[i] = x, y
    speed = 0.35 * rw              # at most a third of the screen per second
    px = _smooth_path(ts, tx - crop_w / 2, rx, rx + rw - crop_w, speed, 0.06 * rw)
    py = _smooth_path(ts, ty - crop_h / 2, ry, ry + rh - crop_h, speed, 0.06 * rh)
    keys = [(round(t, 2), int(x), int(y)) for t, x, y in zip(ts, px, py)][::2]   # every 0.5 s
    return keys, (int(crop_w) // 2 * 2, int(crop_h) // 2 * 2)


def expr(keys, idx):
    """Piecewise-linear ffmpeg expression of time for keyframe column idx (1=x, 2=y)."""
    if not keys:
        return "0"
    vals = [k[idx] for k in keys]
    if max(vals) - min(vals) < 2:
        return str(vals[0])
    parts = [f"lt(t,{keys[0][0]})*{vals[0]}"]
    for (t0, *a), (t1, *b) in zip(keys, keys[1:]):
        v0, v1 = a[idx - 1], b[idx - 1]
        if t1 <= t0:
            continue
        if v0 == v1:
            parts.append(f"gte(t,{t0})*lt(t,{t1})*{v0}")
        else:
            parts.append(f"gte(t,{t0})*lt(t,{t1})*({v0}+({v1 - v0})*(t-{t0})/{round(t1 - t0, 3)})")
    parts.append(f"gte(t,{keys[-1][0]})*{vals[-1]}")
    return "+".join(parts)


def choose_mode(lay, samples, cfg, override=None):
    """Which layout fits this clip."""
    if override in ("split", "pip", "cam", "game"):
        return override, "set in config"
    allowed = set(cfg.get("allowed", ["split", "pip", "cam", "game"]))
    if lay["mode"] == "cam":
        return "cam", lay["how"]
    if lay["mode"] == "game" or not lay.get("cam"):
        return "game", lay["how"]
    W, H = lay["size"]
    cam_w = lay["cam"][2] * 1920 / W          # in 1080p pixels, whatever the source size
    if "pip" in allowed and cam_w < cfg.get("min_cam_width", 300):
        return "pip", f"small facecam ({cam_w:.0f}px wide), gameplay gets the space"
    if "pip" in allowed and cfg.get("pip_when_cam_still", True):
        game_act = activity(samples, lay["size"], lay["cam"])
        # movement inside the camera box vs the game
        cx, cy, cw, ch = lay["cam"]
        cam_only = []
        for (_, im0), (_, im1) in zip(samples, samples[1:]):
            a = cv2.cvtColor(im0[int(cy):int(cy + ch), int(cx):int(cx + cw)], cv2.COLOR_BGR2GRAY).astype(np.float32)
            b = cv2.cvtColor(im1[int(cy):int(cy + ch), int(cx):int(cx + cw)], cv2.COLOR_BGR2GRAY).astype(np.float32)
            cam_only.append(float(np.abs(a - b).mean()))
        g = float(np.mean([a[3] for a in game_act])) if game_act else 0.0
        c = float(np.mean(cam_only)) if cam_only else 0.0
        if g > 6.0 and c < 0.25 * g:
            return "pip", f"all about the game (game motion {g:.1f}, camera {c:.1f})"
    return "split", lay["how"]


def plan(video, lay, cfg=None, override=None):
    """Everything editor.render needs: the layout and how to crop each panel."""
    import editor
    cfg = cfg or {}
    samples = _samples(video, cfg.get("track_fps", 4))
    mode, why = choose_mode(lay, samples, cfg, override)
    size = lay["size"]
    W, H = size
    follow = cfg.get("follow", True)
    boxes = editor.panel_boxes(mode)
    crops = {}

    def tracked(name, aspect, region=None, exclude=None, how="action", zoom=1.0):
        rx, ry, rw, rh = region or (0, 0, W, H)
        ch = rh / zoom
        cw = ch * aspect
        if cw > rw:
            cw, ch = rw, rw / aspect
        if follow:
            keys, (cw2, ch2) = track(samples, size, cw, ch, region, exclude, how)
        else:
            keys, (cw2, ch2) = [(0, int(rx + (rw - cw) / 2), int(ry + (rh - ch) / 2))], (int(cw) // 2 * 2, int(ch) // 2 * 2)
        crops[name] = {"w": cw2, "h": ch2, "x": expr(keys, 1), "y": expr(keys, 2), "src_w": cw2}

    def static(name, aspect):
        x, y, w, h = lay_mod.crop_box(dict(lay, mode="split"), aspect)
        crops[name] = {"w": w, "h": h, "x": str(x), "y": str(y), "src_w": w, "lift": lay.get("dark", False)}

    if mode == "split":
        static("cam", boxes["cam"][2] / boxes["cam"][3])
        z = float(cfg.get("game_zoom", 1.0))
        if z > 1.0:
            tracked("game", 16 / 9, exclude=lay["cam"], zoom=z)
        else:
            crops["game"] = {"whole": True}
    elif mode == "pip":
        main = boxes["main"]
        tracked("main", main[2] / main[3], exclude=lay["cam"], how="action")
        static("bubble", boxes["bubble"][2] / boxes["bubble"][3])
    elif mode == "cam":
        main = boxes["main"]
        tracked("main", main[2] / main[3], how="face")
    else:
        main = boxes["main"]
        tracked("main", main[2] / main[3], how="action", zoom=float(cfg.get("game_zoom", 1.0)))
    return {"mode": mode, "why": why, "crops": crops, "size": size}
