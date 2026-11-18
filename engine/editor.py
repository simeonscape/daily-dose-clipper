"""Turn a raw 16:9 stream segment into a branded vertical Short.

Layout (1080x1920, orange brand):
  top      - logo chip + "DAILY DOSE OF STREAMERS"
  hook     - big white hook text for the first seconds
  middle   - the stream, cropped, inside a white frame
  below    - word-by-word subtitles (active word in brand orange)
  bottom   - left clear for YouTube's own buttons and title
"""
import os
import subprocess
import textwrap
from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ASSETS = os.path.join(HERE, "..", "assets")
W, H = 1080, 1920
ORANGE = (217, 119, 87)
DARK = (193, 95, 64)
BLACK = os.path.join(ASSETS, "InterDisplay-Black.otf")
SEMI = os.path.join(ASSETS, "InterDisplay-SemiBold.otf")

# Video box per crop mode: (crop aspect w/h, box width)
CROPS = {
    "center43": (4 / 3, 1000),   # good default for facecam-in-the-middle streams
    "square": (1.0, 1000),
    "full": (16 / 9, 1040),      # whole frame, e.g. gameplay with a facecam corner
}
VIDEO_TOP = 640


def video_box(crop):
    aspect, bw = CROPS.get(crop, CROPS["center43"])
    bh = int(round(bw / aspect / 2) * 2)
    return (W - bw) // 2, VIDEO_TOP, bw, bh


def make_background(path, hook, streamer, crop, channel="DAILY DOSE OF STREAMERS"):
    """Static layer: orange, brand chip, hook text, white frame for the video."""
    img = Image.new("RGB", (W, H), ORANGE)
    band = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(band)
    for i in range(-4, 10):
        x = i * 260
        d.polygon([(x, 0), (x + 80, 0), (x + 80 - 700, H), (x - 700, H)], fill=26)
    img.paste(DARK, (0, 0), band.filter(ImageFilter.GaussianBlur(2)))
    d = ImageDraw.Draw(img)

    # brand chip
    logo = Image.open(os.path.join(ASSETS, "logo.png")).convert("RGB").resize((84, 84), Image.LANCZOS)
    m = Image.new("L", (336, 336), 0)
    ImageDraw.Draw(m).ellipse((0, 0, 336, 336), fill=255)
    m = m.resize((84, 84), Image.LANCZOS)
    f_chip = ImageFont.truetype(SEMI, 34)
    label = channel
    tw = d.textlength(label, font=f_chip)
    chip_w = 84 + 20 + tw + 36
    cx = int((W - chip_w) / 2)
    cy = 170
    d.rounded_rectangle((cx - 8, cy - 8, cx + chip_w, cy + 92), radius=50, fill=(255, 255, 255))
    img.paste(logo, (cx, cy), m)
    d.text((cx + 104, cy + 42), label, font=f_chip, fill=(170, 78, 48), anchor="lm")

    # hook text: biggest size that fits in at most 3 lines, centred between chip and video
    hook = (hook or "").upper().strip()
    for size in range(112, 46, -4):
        f = ImageFont.truetype(BLACK, size)
        lines, cur = [], ""
        for word in hook.split():
            trial = (cur + " " + word).strip()
            if d.textlength(trial, font=f) <= W - 110:
                cur = trial
            else:
                lines.append(cur); cur = word
        lines.append(cur)
        if (len(lines) <= 3 and len(lines) * size * 1.06 <= VIDEO_TOP - 320
                and all(d.textlength(l, font=f) <= W - 110 for l in lines)):
            break
    lh = size * 1.06
    y = 290 + (VIDEO_TOP - 30 - 290 - lh * len(lines)) / 2
    for line in lines:
        d.text((W / 2 + 4, y + 6), line, font=f, fill=(120, 55, 35), anchor="ma")
        d.text((W / 2, y), line, font=f, fill=(255, 255, 255), anchor="ma")
        y += lh

    # white frame behind the video
    x0, y0, bw, bh = video_box(crop)
    pad = 10
    d.rounded_rectangle((x0 - pad, y0 - pad, x0 + bw + pad, y0 + bh + pad), radius=28, fill=(255, 255, 255))

    # credit under the frame
    f_cr = ImageFont.truetype(SEMI, 30)
    d.text((W / 2, y0 + bh + pad + 22), f"Clip: {streamer}", font=f_cr, fill=(255, 238, 230), anchor="ma")
    img.save(path)
    return y0 + bh + pad + 80   # where subtitles can start


def _ass_time(t):
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def make_subtitles(words, path, top_y, words_per_line=3):
    """Word-by-word captions. `words` is a list of (start, end, text)."""
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,Inter Display Black,92,&H00FFFFFF,&H00FFFFFF,&H00141414,&H64000000,0,0,0,0,100,100,3,0,1,7,3,8,60,60,{int(top_y)},1

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
            lines.append(f"Dialogue: 0,{_ass_time(s)},{_ass_time(end)},Cap,,0,0,0,,{' '.join(parts)}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(head + "\n".join(lines) + "\n")


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


def render(segment, out, hook, streamer, words, crop="center43", workdir=None):
    workdir = workdir or os.path.dirname(os.path.abspath(out))
    bg = os.path.join(workdir, "bg.png")
    ass = os.path.join(workdir, "subs.ass")
    sub_top = make_background(bg, hook, streamer, crop)
    make_subtitles(words, ass, sub_top)
    aspect, bw = CROPS.get(crop, CROPS["center43"])
    x0, y0, bw, bh = video_box(crop)
    vf = (
        f"[0:v]crop='min(iw,ih*{aspect})':'min(ih,iw/{aspect})',scale={bw}:{bh}:flags=lanczos,setsar=1[v];"
        f"[1:v][v]overlay={x0}:{y0}:shortest=1,"
        f"subtitles='{ass}':fontsdir='{ASSETS}',format=yuv420p[out]"
    )
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", segment, "-loop", "1", "-i", bg,
        "-filter_complex", vf, "-map", "[out]", "-map", "0:a?",
        "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-r", "30",
        "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", "-shortest", out,
    ]
    subprocess.run(cmd, check=True)
    return out
