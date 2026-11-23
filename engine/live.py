"""Watch live streams: read chat as it happens, keep the last few minutes of
video, and cut a moment the second chat explodes.

  ChatListener  - Twitch chat over IRC, read-only and anonymous (no login).
  Recorder      - streamlink pulls the live video, ffmpeg cuts it into 6 s
                  files and only the last `buffer_s` seconds are kept.
  Detector      - the same weighting as the VOD chat spikes (CLIP IT 5x,
                  LOL spam 4x, hype 2x), compared with the last few minutes
                  of that same chat, so busy and quiet chats work alike.

The viewer's stream and its chat run a few seconds behind the streamer;
the recorder and the chat are both "viewers", so a chat burst lines up with
the video we recorded `chat_delay_s` seconds earlier.
"""
import collections
import glob
import os
import random
import shutil
import socket
import subprocess
import threading
import time

import spikes


class ChatListener(threading.Thread):
    HOST, PORT = "irc.chat.twitch.tv", 6667

    def __init__(self, login, keep_s=900):
        super().__init__(daemon=True)
        self.login = login.lower()
        self.keep_s = keep_s
        self.messages = collections.deque()      # (wall time, text)
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.connected = False

    def _connect(self):
        s = socket.create_connection((self.HOST, self.PORT), timeout=30)
        nick = f"justinfan{random.randint(10000, 99999)}"         # Twitch's anonymous read-only login
        for line in ("CAP REQ :twitch.tv/tags", "PASS SCHMOOPIIE", f"NICK {nick}", f"JOIN #{self.login}"):
            s.sendall((line + "\r\n").encode())
        s.settimeout(330)
        return s

    @staticmethod
    def parse(line):
        """Text of a chat message line, or None."""
        if " PRIVMSG #" not in line:
            return None
        try:
            return line.split(" PRIVMSG #", 1)[1].split(" :", 1)[1]
        except IndexError:
            return None

    def add(self, text, when=None):
        when = when or time.time()
        with self.lock:
            self.messages.append((when, text))
            while self.messages and self.messages[0][0] < when - self.keep_s:
                self.messages.popleft()

    def window(self, a, b):
        with self.lock:
            return [(t, m) for t, m in self.messages if a <= t <= b]

    def run(self):
        while not self.stop.is_set():
            try:
                sock = self._connect()
                self.connected = True
                buf = b""
                while not self.stop.is_set():
                    data = sock.recv(65536)
                    if not data:
                        break
                    buf += data
                    *lines, buf = buf.split(b"\r\n")
                    for raw in lines:
                        line = raw.decode("utf-8", "replace")
                        if line.startswith("PING"):
                            sock.sendall(b"PONG :tmi.twitch.tv\r\n")
                            continue
                        text = self.parse(line)
                        if text is not None:
                            self.add(text)
            except OSError as e:
                print(f"[{self.login}] chat connection dropped ({e}), reconnecting")
            self.connected = False
            time.sleep(5)


class Recorder:
    """Keeps the last `buffer_s` seconds of a live stream as 6 s files."""

    SEG = 6

    def __init__(self, login, workdir, buffer_s=240, quality="best", source_cmd=None):
        self.login = login
        self.dir = os.path.join(workdir, f"live_{login}")
        shutil.rmtree(self.dir, ignore_errors=True)
        os.makedirs(self.dir)
        self.buffer_s = buffer_s
        self.quality = quality
        self.source_cmd = source_cmd        # for tests: a command writing MPEG-TS to stdout
        self.procs = []
        self.known = {}                     # file -> (wall start, wall end)
        self.list_path = os.path.join(self.dir, "list.csv")

    def start(self):
        src = self.source_cmd or [shutil.which("streamlink") or "streamlink", "--stdout", "--twitch-disable-ads",
                                  "--stream-segment-threads", "2", "--retry-open", "3",
                                  f"https://twitch.tv/{self.login}", f"{self.quality},best"]
        a = subprocess.Popen(src, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        b = subprocess.Popen(["ffmpeg", "-loglevel", "error", "-i", "pipe:0", "-map", "0:v:0", "-map", "0:a:0?",
                              "-c", "copy", "-f", "segment", "-segment_time", str(self.SEG),
                              "-segment_list", self.list_path, "-segment_list_type", "csv",
                              "-reset_timestamps", "1", os.path.join(self.dir, "s%06d.ts")],
                             stdin=a.stdout, stderr=subprocess.DEVNULL)
        a.stdout.close()
        self.procs = [a, b]

    def alive(self):
        return all(p.poll() is None for p in self.procs)

    def poll(self):
        """Stamp newly finished files with wall-clock times. ffmpeg lists a file
        the moment it's done, and a live stream arrives in real time, so its
        end is about now. Call this every second or so."""
        if not os.path.exists(self.list_path):
            return
        with open(self.list_path) as f:
            rows = [r.strip().split(",") for r in f if r.strip()]
        now = time.time()
        for parts in rows:
            if len(parts) >= 3 and parts[0] not in self.known:
                dur = float(parts[2]) - float(parts[1])
                self.known[parts[0]] = (now - dur, now)

    def segments(self):
        """[(wall start, wall end, path)] of finished files still on disk."""
        self.poll()
        out = []
        for name, (a, b) in sorted(self.known.items(), key=lambda kv: kv[1][0]):
            p = os.path.join(self.dir, name)
            if os.path.exists(p):
                out.append((a, b, p))
        return out

    def prune(self, keep=()):
        cutoff = time.time() - self.buffer_s
        for a, b, p in self.segments():
            if b < cutoff and p not in keep:
                try:
                    os.remove(p)
                except OSError:
                    pass

    def cut(self, a, b, out):
        """Video between wall times a and b, as one mp4. Returns the actual start (wall)."""
        segs = [s for s in self.segments() if s[1] > a and s[0] < b]
        if not segs:
            raise RuntimeError("that part of the stream isn't in the buffer any more")
        lst = out + ".txt"
        with open(lst, "w") as f:
            for _, _, p in segs:
                f.write(f"file '{p}'\n")
        first = segs[0][0]
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst,
                        "-ss", f"{max(0.0, a - first):.2f}", "-t", f"{b - max(a, first):.2f}",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-c:a", "aac", "-b:a", "192k",
                        out], check=True)
        os.remove(lst)
        return max(a, first)

    def stop(self):
        for p in self.procs:
            if p.poll() is None:
                p.terminate()
        for p in self.procs:
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()


class Detector:
    """Chat spikes in real time."""

    def __init__(self, bin_s=2.0, window_s=10.0, baseline_s=300.0, warmup_s=120.0,
                 min_score=4.0, min_gap_s=90.0):
        self.bin_s, self.window_s, self.baseline_s = bin_s, window_s, baseline_s
        self.warmup_s, self.min_score, self.min_gap_s = warmup_s, min_score, min_gap_s
        self.open = None          # spike being tracked: dict(t, z)
        self.last = 0.0
        self.started = None

    def z_now(self, messages, now):
        """z of the last `window_s` seconds of chat vs the last `baseline_s`."""
        n = int(self.baseline_s // self.bin_s)
        bins = [0.0] * n
        for t, body in messages:
            i = int((now - t) // self.bin_s)
            if 0 <= i < n:
                bins[n - 1 - i] += spikes.WEIGHTS[spikes.kind(body)]
        # only time we were actually listening counts as "normal" chat
        seen = int((now - (self.started or now)) // self.bin_s) + 1
        bins = bins[max(0, n - seen):]
        n = len(bins)
        w = max(1, int(self.window_s // self.bin_s))
        if n < w:
            return 0.0, 0.0
        act = [sum(bins[i:i + w]) for i in range(0, n - w + 1)]
        ref = sorted(act)
        med = ref[len(ref) // 2]
        mad = sorted(abs(x - med) for x in ref)[len(ref) // 2] or 1.0
        return (act[-1] - med) / (1.4826 * mad), act[-1]

    def update(self, messages, now=None):
        """Feed it the chat every second or so. Returns a finished spike
        {t: wall time of the peak, z: score} once chat calms down again."""
        now = now or time.time()
        if self.started is None:
            self.started = now
        if now - self.started < self.warmup_s:
            return None
        z, _ = self.z_now(messages, now)
        if self.open:
            if z > self.open["z"]:
                self.open.update(t=now - self.window_s / 2, z=z)
            if z < 0.5 * self.open["z"] or now - self.open["t"] > 40:
                done, self.open = self.open, None
                self.last = done["t"]
                return done
        elif z >= self.min_score and now - self.last >= self.min_gap_s:
            self.open = {"t": now - self.window_s / 2, "z": z}
        return None


def wait_for(recorder, wall, timeout=60):
    """Wait until the recorder has written video up to `wall`."""
    end = time.time() + timeout
    while time.time() < end:
        segs = recorder.segments()
        if segs and segs[-1][1] >= wall:
            return True
        time.sleep(2)
    return False


def tidy(path):
    for f in glob.glob(os.path.join(path, "*")):
        try:
            os.remove(f)
        except OSError:
            pass
