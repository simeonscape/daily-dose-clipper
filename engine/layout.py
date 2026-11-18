"""Find where the streamer's camera is in a stream clip.

Returns one of three layouts:
  split - a facecam overlay on top of gameplay: camera on top, whole screen below
  cam   - the whole screen is the camera (Just Chatting / IRL): one big panel
  game  - no camera found: one big panel of the stream

How it finds the camera:
  1. Faces. A face detector runs on frames spread over the clip. A real webcam
     face sits in roughly the same place in most frames; game characters move
     around or come and go, so they're ignored.
  2. The overlay's border. A webcam overlay is a rectangle whose edges stay put
     for the whole clip while the gameplay around it changes, so its border
     shows up as long straight lines in the frames' average edges.
  A dark camera (no face found) can still be found from its border alone.

A streamer's camera can also be set by hand in config.yaml with
`cam: [x, y, width, height]` as fractions of the screen (0 to 1).
"""
import os

import cv2
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL = os.path.join(HERE, "..", "assets", "face_detection_yunet_2023mar.onnx")
AW, AH = 960, 540          # analysis size


def _frames(video, n=16):
    cap = cv2.VideoCapture(video)
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    dur = total / fps if total else 60
    out = []
    for i in range(n):
        cap.set(cv2.CAP_PROP_POS_MSEC, (0.5 + i) / n * dur * 1000)
        ok, im = cap.read()
        if ok:
            out.append(im)
    cap.release()
    if not out:
        raise RuntimeError(f"Couldn't read frames from {video}")
    return out


def _faces(frames, size):
    """All confident faces per frame, in source pixels, on brightened frames too."""
    w, h = size
    det = cv2.FaceDetectorYN.create(MODEL, "", (w, h), 0.6, 0.3, 50)
    lut = np.array([((i / 255.0) ** 0.5) * 255 for i in range(256)], np.uint8)  # lifts dark cams
    per_frame = []
    for im in frames:
        found = []
        for img in (im, cv2.LUT(im, lut)):
            det.setInputSize((img.shape[1], img.shape[0]))
            _, f = det.detect(img)
            if f is not None:
                found += [tuple(map(float, x[:4])) + (float(x[-1]),) for x in f]
        per_frame.append([f for f in found if f[2] >= 0.015 * w])
    return per_frame


def _stable_face(per_frame):
    """The face that stays in about the same place for most of the clip."""
    clusters = []   # each: list of (frame_index, face)
    for i, faces in enumerate(per_frame):
        for f in faces:
            cx, cy = f[0] + f[2] / 2, f[1] + f[3] / 2
            for c in clusters:
                mx = np.median([g[0] + g[2] / 2 for _, g in c])
                my = np.median([g[1] + g[3] / 2 for _, g in c])
                mw = np.median([g[2] for _, g in c])
                if abs(cx - mx) < 0.8 * mw and abs(cy - my) < 0.8 * mw and 0.6 < f[2] / mw < 1.7:
                    c.append((i, f)); break
            else:
                clusters.append([(i, f)])
    best = None
    for c in clusters:
        frames_hit = len({i for i, _ in c})
        if frames_hit >= max(3, 0.45 * len(per_frame)):
            if best is None or frames_hit > len({i for i, _ in best}):
                best = c
    if not best:
        return None
    fs = np.array([f[:4] for _, f in best])
    x, y, w, h = np.median(fs, axis=0)
    return x, y, w, h


def _lines(edge, axis, min_len):
    """Long straight runs in a persistent edge map. axis=0: vertical lines."""
    e = edge if axis == 0 else edge.T          # scan columns of e
    thr = max(30.0, 4 * float(np.median(e)))
    strong = e > thr
    # allow 1px wobble
    strong = strong | np.roll(strong, 1, axis=1) | np.roll(strong, -1, axis=1)
    lines = []
    for x in range(2, e.shape[1] - 2):
        col = strong[:, x]
        y = 0
        n = len(col)
        while y < n:
            if col[y]:
                s, gap = y, 0
                while y < n and gap <= 4:
                    gap = 0 if col[y] else gap + 1
                    y += 1
                end = y - gap
                if end - s >= min_len:
                    lines.append((x, s, end, float(e[s:end, x].mean())))
            y += 1
    # keep the strongest of lines sitting next to each other
    lines.sort(key=lambda l: -l[3] * (l[2] - l[1]))
    kept = []
    for l in lines:
        if all(abs(l[0] - k[0]) > 4 or min(l[2], k[2]) - max(l[1], k[1]) < 0.5 * (l[2] - l[1]) for k in kept):
            kept.append(l)
    return kept[:200]


def _coverage(lines, pos, a, b, limit, tol=4):
    """How much of [a, b] at position pos is covered by lines (frame edges count fully)."""
    if pos <= 2 or pos >= limit - 3:
        return 1.0
    best = 0.0
    for p, s, e, _ in lines:
        if abs(p - pos) <= tol:
            best = max(best, max(0, min(e, b) - max(s, a)) / max(1, b - a))
    return best


def _rectangles(vert, horiz, std, face):
    """Rectangles drawn by persistent lines (or the screen edge) that hold the face."""
    fx, fy, fw, fh = face
    cx, cy = fx + fw / 2, fy + fh / 2      # the face box can poke past a tight camera
    near = lambda p, lo, hi: lo <= p <= hi
    lefts = [0] + [l[0] for l in vert if near(l[0], cx - 6 * fw, cx - 0.3 * fw)]
    rights = [AW - 1] + [l[0] for l in vert if near(l[0], cx + 0.3 * fw, cx + 6 * fw)]
    tops = [0] + [l[0] for l in horiz if near(l[0], cy - 4 * fh, cy - 0.3 * fh)]
    bottoms = [AH - 1] + [l[0] for l in horiz if near(l[0], cy + 0.3 * fh, cy + 5 * fh)]
    rects = []
    for x1 in set(lefts):
        for x2 in set(rights):
            w = x2 - x1
            if not (0.12 * AW <= w <= 0.7 * AW):
                continue
            for y1 in set(tops):
                for y2 in set(bottoms):
                    h = y2 - y1
                    if not (0.15 * AH <= h <= 0.8 * AH) or not (0.8 <= w / h <= 2.4):
                        continue
                    cov = [_coverage(vert, x1, y1, y2, AW), _coverage(vert, x2, y1, y2, AW),
                           _coverage(horiz, y1, x1, x2, AH), _coverage(horiz, y2, x1, x2, AH)]
                    con = [_contrast(std, "v", x1, y1, y2, 1), _contrast(std, "v", x2, y1, y2, -1),
                           _contrast(std, "h", y1, x1, x2, 1), _contrast(std, "h", y2, x1, x2, -1)]
                    real = [c for c in con if c is not None]
                    if len(real) < 2:
                        continue
                    # a side counts if it's a solid line, or if it clearly splits still from moving
                    sides = [cv if cn is None else max(cv, min(1.0, 2 * cn)) for cv, cn in zip(cov, con)]
                    if min(sides) >= 0.6:
                        rects.append((sum(sides) / 4, sum(real) / len(real), (x1, y1, x2, y2)))
    return rects


def _persistent_edges(frames):
    """Edges that stay put. Sums three cues: the average edge strength, the edges
    of the average frame (moving gameplay blurs to grey, a camera doesn't), and
    the edges of how much each pixel changes (gameplay changes far more)."""
    g = np.stack([cv2.cvtColor(cv2.resize(im, (AW, AH), interpolation=cv2.INTER_AREA),
                               cv2.COLOR_BGR2GRAY).astype(np.float32) for im in frames])
    sob = lambda a, dx, dy: np.abs(cv2.Sobel(a, cv2.CV_32F, dx, dy, ksize=3))
    mean, std = g.mean(0), g.std(0)
    gx = np.mean([sob(f, 1, 0) for f in g], 0) + sob(mean, 1, 0) + 2 * sob(std, 1, 0)
    gy = np.mean([sob(f, 0, 1) for f in g], 0) + sob(mean, 0, 1) + 2 * sob(std, 0, 1)
    return gx, gy, std


def _contrast(std, side, pos, a, b, inward, k=10):
    """How differently the two sides of a border change over time (0 to 1).
    A camera next to gameplay: one side barely changes, the other changes a lot."""
    a, b, lim = int(a), int(b), (AW if side == "v" else AH)
    if pos <= 2 or pos >= lim - 3:
        return None
    m = std if side == "v" else std.T
    before, after = m[a:b, max(0, pos - 3 - k):pos - 3], m[a:b, pos + 3:pos + 3 + k]
    inn, out = (after, before) if inward > 0 else (before, after)
    if inn.size == 0 or out.size == 0:
        return None
    i, o = float(inn.mean()), float(out.mean())
    return abs(o - i) / (o + i + 8)


def find_layout(video, override=None, debug_path=None):
    frames = _frames(video)
    H, W = frames[0].shape[:2]
    sx, sy = W / AW, H / AH

    if override:
        x, y, w, h = override
        return {"mode": "split", "cam": (x * W, y * H, w * W, h * H), "size": (W, H), "how": "set in config"}

    face = _stable_face(_faces(frames, (W, H)))
    res = {"mode": "game", "size": (W, H), "how": "no camera found"}
    if face:
        fa = (face[0] / sx, face[1] / sy, face[2] / sx, face[3] / sy)
        gx, gy, std = _persistent_edges(frames)
        rects = _rectangles(_lines(gx, 0, int(0.12 * AH)), _lines(gy, 1, int(0.10 * AW)), std, fa)
        cam = how = None
        good = [(cov * (0.3 + con), r) for cov, con, r in rects if cov >= 0.7 and con >= 0.15]
        if good:
            # the box whose border is most solid and best separates camera from gameplay
            cam = max(good)[1]
            how = "face + overlay border"
        if not cam and face[2] >= 0.11 * W:
            res = {"mode": "cam", "face": face, "size": (W, H), "how": "big face, full-screen camera"}
        else:
            if not cam:
                # no border found: a box around the face, kept on screen
                fx, fy, fw, fh = fa
                bw = min(4.5 * fw, AW * 0.45); bh = bw / 1.5
                cx, cy = fx + fw / 2, fy + fh * 0.9
                x1 = min(max(0, cx - bw / 2), AW - bw); y1 = min(max(0, cy - bh / 2), AH - bh)
                cam, how = (x1, y1, x1 + bw, y1 + bh), "face only"
            x1, y1, x2, y2 = cam
            res = {"mode": "split", "cam": (x1 * sx, y1 * sy, (x2 - x1) * sx, (y2 - y1) * sy),
                   "face": face, "size": (W, H), "how": how}

    if res.get("cam"):
        # a dark camera (like a streamer in a dim room) gets brightened in the Short
        x, y, w, h = (int(v) for v in res["cam"])
        luma = np.mean([cv2.cvtColor(f[y:y + h, x:x + w], cv2.COLOR_BGR2GRAY).mean() for f in frames])
        res["dark"] = bool(luma < 60)

    if debug_path:
        dbg = frames[len(frames) // 2].copy()
        if res.get("cam"):
            x, y, w, h = map(int, res["cam"])
            cv2.rectangle(dbg, (x, y), (x + w, y + h), (0, 255, 0), 4)
        if res.get("face"):
            x, y, w, h = map(int, res["face"])
            cv2.rectangle(dbg, (x, y), (x + w, y + h), (0, 0, 255), 3)
        cv2.putText(dbg, f"{res['mode']}: {res['how']}", (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.4, (0, 255, 255), 3)
        cv2.imwrite(debug_path, dbg)
    return res


def crop_box(res, aspect, face_y=0.42):
    """A crop of the camera (or the screen) with the given width/height ratio."""
    W, H = res["size"]
    face = res.get("face")
    if res["mode"] == "split":
        x, y, w, h = res["cam"]
    else:
        x, y, w, h = 0, 0, W, H
    if w / h > aspect:            # too wide: trim the sides, keep the face in view
        nw = h * aspect
        cx = face[0] + face[2] / 2 if face else x + w / 2
        nx = min(max(x, cx - nw / 2), x + w - nw)
        box = (nx, y, nw, h)
    else:                         # too tall: trim top/bottom, face a bit above middle
        nh = w / aspect
        cy = face[1] + face[3] / 2 if face else y + h * 0.5
        ny = min(max(y, cy - nh * face_y), y + h - nh)
        box = (x, ny, w, nh)
    bx, by, bw, bh = (int(round(v)) for v in box)
    bw -= bw % 2; bh -= bh % 2
    return bx, by, bw, bh
