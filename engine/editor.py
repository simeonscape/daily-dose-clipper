"""Turn a raw 16:9 stream segment into a branded vertical Short.

Layout (1080x1920, orange brand):
  top     - the streamer's picture and name, then the hook in big white text
  split   - when the stream has a facecam: the camera on top, the whole
            screen below, subtitles on the line between them
  pip     - gameplay in one tall panel that follows the action, the facecam
            as a small box in its corner
  cam     - full-screen camera (Just Chatting): one tall panel following the face
  game    - no camera: one big panel following the action
framing.py decides which one fits each clip and how to crop.
  bottom  - small channel chip, under where YouTube shows its title
"""
import os
import subprocess
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "..", "assets")
W, H = 1080, 1920
ORANGE = (217, 119, 87)
DARK = (193, 95, 64)
INK = (150, 66, 40)
BLACK = os.path.join(ASSETS, "InterDisplay-Black.otf")
SEMI = os.path.join(ASSETS, "InterDisplay-SemiBold.otf")

PX, PW = 30, 1020              # panels: left edge and width
TOP = 350                      # first panel starts here
CAM_H = 680                    # split: camera panel height (aspect 1.5)
GAME_H = 574                   # split: whole screen, 16:9
GAP = 26
SINGLE_H = {"cam": 1264, "game": 1020, "pip": 1264}
BUBBLE = (440, 330)            # pip: the facecam box in the corner
BORDER = 8


def panel_boxes(mode):
    """{name: (x, y, w, h)} of the video panels, drawn in this order."""
    if mode == "split":
        return {"cam": (PX, TOP, PW, CAM_H), "game": (PX, TOP + CAM_H + GAP, PW, GAME_H)}
    h = SINGLE_H.get(mode, SINGLE_H["game"])
    boxes = {"main": (PX, TOP, PW, h)}
    if mode == "pip":
        bw, bh = BUBBLE
        boxes["bubble"] = (PX + 28, TOP + h - 28 - bh, bw, bh)
    return boxes


def panels(mode):
    """[(x, y, w, h)] for the framed panels, and the y where subtitles are centred."""
    if mode == "split":
        b = panel_boxes(mode)
        return [b["cam"], b["game"]], TOP + CAM_H + GAP // 2
    h = SINGLE_H.get(mode, SINGLE_H["game"])
    if mode == "pip":
        sub_y = TOP + h - 28 - BUBBLE[1] - 100
    else:
        sub_y = TOP + h - 230 if mode == "cam" else TOP + h + 110
    return [(PX, TOP, PW, h)], sub_y


def _circle(img, size):
    img = img.convert("RGBA").resize((size * 3, size * 3), Image.LANCZOS)
    m = Image.new("L", img.size, 0)
    ImageDraw.Draw(m).ellipse((0, 0, img.size[0], img.size[1]), fill=255)
    img.putalpha(m)
    return img.resize((size, size), Image.LANCZOS)


def _fit_lines(d, text, font_path, max_w, max_lines, max_h, start, stop):
    for size in range(start, stop, -4):
        f = ImageFont.truetype(font_path, size)
        lines, cur = [], ""
        for word in text.split():
            trial = (cur + " " + word).strip()
            if d.textlength(trial, font=f) <= max_w:
                cur = trial
            else:
                lines.append(cur); cur = word
        lines.append(cur)
        if (len(lines) <= max_lines and len(lines) * size * 1.05 <= max_h
                and all(d.textlength(l, font=f) <= max_w for l in lines)):
            return f, size, lines
    return f, size, lines


def make_background(path, hook, streamer, mode, login=None, channel="DAILY DOSE OF STREAMERS"):
    """Static layer: orange, streamer badge, hook, white frames for the video panels."""
    img = Image.new("RGB", (W, H), ORANGE)
    band = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(band)
    for i in range(-4, 10):
        x = i * 260
        d.polygon([(x, 0), (x + 80, 0), (x + 80 - 700, H), (x - 700, H)], fill=26)
    img.paste(DARK, (0, 0), band.filter(ImageFilter.GaussianBlur(2)))
    d = ImageDraw.Draw(img)

    # streamer badge: their picture + name, so viewers see who it is right away
    pic_path = os.path.join(ASSETS, "streamers", f"{login}.png") if login else None
    f_name = ImageFont.truetype(BLACK, 52)
    name_w = d.textlength(streamer, font=f_name)
    pic = 120 if pic_path and os.path.exists(pic_path) else 0
    pill_h = 86
    total = (pic - 26 if pic else 0) + name_w + 64
    bx = int((W - total) / 2)
    by = 60
    px0 = bx + (pic - 26 if pic else 0)
    d.rounded_rectangle((px0, by + (pic - pill_h) // 2 if pic else by, px0 + name_w + 64,
                         (by + (pic - pill_h) // 2 if pic else by) + pill_h), radius=pill_h // 2, fill=(255, 255, 255))
    name_cy = by + pic // 2 if pic else by + pill_h // 2
    d.text((px0 + (26 if pic else 0) + 32, name_cy), streamer, font=f_name, fill=INK, anchor="lm")
    if pic:
        ring = Image.new("RGBA", (pic + 12, pic + 12), (0, 0, 0, 0))
        ImageDraw.Draw(ring).ellipse((0, 0, pic + 11, pic + 11), fill=(255, 255, 255, 255))
        img.paste(ring, (bx - 6, by - 6), ring)
        c = _circle(Image.open(pic_path), pic)
        img.paste(c, (bx, by), c)

    # hook: biggest size that fits in two lines between badge and video
    hook = (hook or "").upper().strip()
    top, bottom = by + max(pic, pill_h) + 14, TOP - 18
    f, size, lines = _fit_lines(d, hook, BLACK, W - 90, 2, bottom - top, 92, 46)
    lh = size * 1.05
    y = top + (bottom - top - lh * len(lines)) / 2
    for line in lines:
        d.text((W / 2 + 4, y + 6), line, font=f, fill=(120, 55, 35), anchor="ma")
        d.text((W / 2, y), line, font=f, fill=(255, 255, 255), anchor="ma")
        y += lh

    # white frames behind the video panels
    boxes, sub_y = panels(mode)
    for x0, y0, bw, bh in boxes:
        d.rounded_rectangle((x0 - 8, y0 - 8, x0 + bw + 8, y0 + bh + 8), radius=26, fill=(255, 255, 255))

    # small channel chip near the bottom
    last = boxes[-1][1] + boxes[-1][3]
    logo = _circle(Image.open(os.path.join(ASSETS, "logo.png")), 56)
    f_chip = ImageFont.truetype(SEMI, 26)
    tw = d.textlength(channel, font=f_chip)
    cw = 56 + 14 + tw + 26
    cx, cy = int((W - cw) / 2), max(last + 40, 1660) if mode == "split" else last + 230
    cy = min(cy, H - 120)
    d.rounded_rectangle((cx - 6, cy - 6, cx + cw, cy + 62), radius=34, fill=(255, 255, 255))
    img.paste(logo, (cx, cy), logo)
    d.text((cx + 70, cy + 28), channel, font=f_chip, fill=INK, anchor="lm")
    img.save(path)
    return sub_y


def _ass_time(t):
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def make_subtitles(words, path, center_y, words_per_line=3):
    """Word-by-word captions. `words` is a list of (start, end, text)."""
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Inter Display Black,84,&H00FFFFFF,&H00FFFFFF,&H00141414,&H64000000,0,0,0,0,100,100,3,0,1,7,3,5,60,60,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    # ASS colours are &HBBGGRR
    hi = "&H{:02X}{:02X}{:02X}&".format(102, 214, 255)  # warm yellow pops on orange
    lines = []
    chunks = [words[i:i + words_per_line] for i in range(0, len(words), words_per_line)]
    for chunk in chunks:
        for j, (s, e, _) in enumerate(chunk):
            end = chunk[j + 1][0] if j + 1 < len(chunk) else e
            parts = []
            for k, (_, _, w) in enumerate(chunk):
                w = w.strip().upper().replace("{", "").replace("}", "")
                parts.append(f"{{\\c{hi}\\fscx108\\fscy108}}{w}{{\\c&HFFFFFF&\\fscx100\\fscy100}}" if k == j else w)
            pos = f"{{\\an5\\pos({W // 2},{int(center_y)})}}"
            lines.append(f"Dialogue: 0,{_ass_time(s)},{_ass_time(end)},Cap,,0,0,0,,{pos}{' '.join(parts)}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(head + "\n".join(lines) + "\n")


def _probe(args, path):
    return subprocess.run(["ffprobe", "-v", "error", *args, "-of", "json", path],
                          capture_output=True, text=True, check=True).stdout


def normalize(segment):
    """Make video and sound start together at 0, with a picture on the first frame.

    Downloaded stream parts often start mid-way through a group of video
    frames: the first few seconds of video can't be decoded (no keyframe yet)
    while the sound plays from the very start. The old fix re-timed picture
    and sound separately, which moved the sound 2 to 5 seconds late. Now both
    are cut at the first real picture (or the start of the sound, whichever
    is later) with one shared clock, so they stay in sync."""
    import json
    info = json.loads(_probe(["-show_entries", "stream=codec_type,start_time:format=start_time"], segment))
    fmt0 = float((info.get("format") or {}).get("start_time") or 0)
    a0 = next((float(st["start_time"]) for st in info.get("streams", [])
               if st.get("codec_type") == "audio" and st.get("start_time") not in (None, "N/A")), fmt0)
    # the first frame that actually decodes
    frames = json.loads(_probe(["-select_streams", "v:0", "-read_intervals", "%+#1",
                                "-show_entries", "frame=pts_time,best_effort_timestamp_time"], segment))
    f0 = (frames.get("frames") or [{}])[0]
    v0 = float(f0.get("pts_time") or f0.get("best_effort_timestamp_time") or fmt0)
    start = max(0.0, max(v0, a0) - fmt0) + 0.05
    fixed = segment.replace(".mp4", "_n.mp4")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{start:.3f}", "-i", segment,
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-c:a", "aac", "-b:a", "192k",
                    "-avoid_negative_ts", "make_zero", fixed], check=True)
    return fixed


def transcribe(video, model_size="small"):
    """Word timestamps with faster-whisper. Returns (words, full_text)."""
    import numpy as np
    from faster_whisper import WhisperModel
    # Decode the audio with ffmpeg ourselves (16 kHz mono), so faster-whisper
    # doesn't depend on whichever PyAV version pip happened to install.
    pcm = subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-i", video,
                          "-vn", "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
                         capture_output=True, check=True).stdout
    audio = np.frombuffer(pcm, np.int16).astype(np.float32) / 32768.0
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segs, _ = model.transcribe(audio, word_timestamps=True, vad_filter=True)
    words, text = [], []
    for seg in segs:
        text.append(seg.text.strip())
        for w in seg.words or []:
            words.append((w.start, w.end, w.word))
    return words, " ".join(text)


def _upscale(src_w, w, h):
    """Scale a crop to its panel. A small webcam gets blown up a lot, so clean up
    compression noise first and sharpen after, which keeps faces crisp."""
    f = w / max(1, src_w)
    if f <= 1.15:
        return f"scale={w}:{h}:flags=lanczos,setsar=1"
    amount = min(1.4, 0.5 + 0.35 * f)
    return (f"hqdn3d=1.5:1.5:4:4,scale={w}:{h}:flags=lanczos,"
            f"unsharp=5:5:{amount:.2f}:5:5:0,setsar=1")


def _crop_chain(c, bw, bh):
    """ffmpeg filters for one panel from a framing.plan() crop."""
    if c.get("whole"):
        return f"crop='min(iw,ih*16/9)':'min(ih,iw*9/16)',scale={bw}:{bh}:flags=lanczos,setsar=1"
    lift = ",eq=gamma=1.7:saturation=1.1" if c.get("lift") else ""
    return f"crop={c['w']}:{c['h']}:x='{c['x']}':y='{c['y']}',{_upscale(c['src_w'], bw, bh)}{lift}"


def render(segment, out, hook, streamer, words, plan, login=None, workdir=None):
    """`plan` comes from framing.plan(): the layout and how to crop each panel."""
    workdir = workdir or os.path.dirname(os.path.abspath(out))
    bg = os.path.join(workdir, "bg.png")
    ass = os.path.join(workdir, "subs.ass")
    mode = plan["mode"]
    sub_y = make_background(bg, hook, streamer, mode, login=login)
    make_subtitles(words, ass, sub_y)
    boxes = panel_boxes(mode)
    names = [n for n in boxes if n in plan["crops"]]
    parts = [f"[0:v]split={len(names)}" + "".join(f"[s{i}]" for i in range(len(names))) if len(names) > 1
             else "[0:v]null[s0]"]
    for i, n in enumerate(names):
        bw, bh = boxes[n][2], boxes[n][3]
        parts.append(f"[s{i}]{_crop_chain(plan['crops'][n], bw, bh)}[p{i}]")
    cur = "[1:v]"
    for i, n in enumerate(names):
        x0, y0, bw, bh = boxes[n]
        nxt = f"[o{i}]"
        parts.append(f"{cur}[p{i}]overlay={x0}:{y0}:shortest=1:eof_action=endall{nxt}")
        cur = nxt
    post = []
    if "bubble" in names:
        x0, y0, bw, bh = boxes["bubble"]
        post.append(f"drawbox=x={x0 - BORDER}:y={y0 - BORDER}:w={bw + 2 * BORDER}:h={bh + 2 * BORDER}"
                    f":color=white:t={BORDER}")
    post.append(f"subtitles='{ass}':fontsdir='{ASSETS}',format=yuv420p[out]")
    parts.append(f"{cur}{','.join(post)}")
    vf = ";".join(parts)
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", segment, "-loop", "1", "-i", bg,
        "-filter_complex", vf, "-map", "[out]", "-map", "0:a?",
        "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
        "-c:v", "libx264", "-preset", "slow", "-crf", "16", "-r", "30",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest", out,
    ]
    subprocess.run(cmd, check=True)
    return out
