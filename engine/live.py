"""Live chat, read as it happens.

  ChatListener  - Twitch chat over IRC, read-only and anonymous (no login).
  Detector      - how far chat is above its own normal (robust z-score), with
                  the same weighting as the VOD chat spikes.
  RateTrigger   - chat speed per 1,000 viewers; fires on a viral moment,
                  but only when chat is reacting (LOL, CLIP IT, OMG, DAMN...).

watcher.py uses these to watch the streamers' chats around the clock and
signal the clipper (the "Clip now" workflow) when one explodes.
"""
import collections
import random
import re
import socket
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


# Words that mean chat is reacting to something (a funny, shocking or hype
# moment), not just talking. Emoji have no word boundaries, so they're matched
# on their own.
REACTION = re.compile(
    r"\b(LO+L+|LUL\w*|KEKW|KEKL|OMEGALUL|ICANT|LMA+O+|LMFAO|LFMAO|ROFL|HA(HA)+|XD+|"
    r"CLIP+\s*(IT|THAT|THIS)?|CLIPP+ED|CLIPPING|O+MG+|OH MY GOD|DA+MN+|DAYU+M+|WTF|WHAT THE|"
    r"NO WAY|NO SHOT|HOLY|INSANE|CRAZY|BRO WHAT|NAH+|DEAD|CRYING|DYING|"
    r"Pog\w*|POGGERS|monka\w*|LETS+ GO+|GOAT(ED)?|W+ ?(CLIP|MOMENT)|SHEESH)\b"
    r"|😂|🤣|💀|😭|😱|😳|🔥|🤯", re.IGNORECASE)
# Hellos and stream-start chat never count, even with a reaction word in them
GREETING = re.compile(r"\b(hi+|hello+|hey+|heyo|yo+|sup|wassup|good (morning|evening|afternoon)|gm|welcome|"
                      r"first|early|we'?re live|he'?s live|stream'?s? (up|live|starting)|"
                      r"W stream|L stream)\b|👋|🫡", re.IGNORECASE)


def is_reaction(text):
    """Is this chat message a reaction (LOL, CLIP IT, OMG, DAMN...) and not a hello?"""
    return bool(REACTION.search(text)) and not GREETING.search(text)


class RateTrigger:
    """Chat speed per 1,000 viewers: messages a minute (measured over the last
    `window_s` seconds) divided by viewers / 1000.

    A moment fires when the rate goes over `threshold` AND chat is clearly
    faster than this stream's own normal (`min_z`), so a chat that's always
    fast doesn't fire all day, AND chat is reacting: at least
    `min_reaction_share` of the messages (and `min_reactions` of them) are
    LOL / CLIP IT / OMG / DAMN-type messages, not hellos. That keeps out a
    stream start where everybody says hello. It reports the peak once chat
    slows down again.
    """

    def __init__(self, threshold=100.0, window_s=20.0, min_z=3.0, cooldown_s=120.0,
                 min_reaction_share=0.3, min_reactions=8):
        self.threshold, self.window_s, self.min_z, self.cooldown_s = threshold, window_s, min_z, cooldown_s
        self.min_reaction_share, self.min_reactions = min_reaction_share, min_reactions
        self.z = Detector(warmup_s=90, window_s=window_s)
        self.open = None
        self.last = 0.0
        self.peak_rate = 0.0       # highest rate seen in this watch (for the logs)
        self.rates = collections.deque(maxlen=3600)

    def rate(self, messages, now, viewers):
        n = sum(1 for t, _ in messages if now - self.window_s < t <= now)
        return n * 60.0 / self.window_s / max(1.0, viewers / 1000.0)

    def reactions(self, messages, now):
        """(reaction messages, share of all messages) over the last `window_s` seconds."""
        recent = [m for t, m in messages if now - self.window_s < t <= now]
        n = sum(1 for m in recent if is_reaction(m))
        return n, n / max(1, len(recent))

    def update(self, messages, now, viewers):
        """Returns a finished moment {t, rate, z, reactions, reaction_share} or None.
        Call every second."""
        if self.z.started is None:
            self.z.started = now
        r = self.rate(messages, now, viewers)
        self.rates.append(r)
        self.peak_rate = max(self.peak_rate, r)
        warm = now - self.z.started >= self.z.warmup_s
        if self.open:
            n, share = self.reactions(messages, now)
            if share > self.open["reaction_share"]:
                self.open.update(reactions=max(n, self.open["reactions"]), reaction_share=share)
            if r > self.open["rate"]:
                z, _ = self.z.z_now(messages, now)
                self.open.update(t=now - self.window_s / 2, rate=r, z=max(z, self.open["z"]))
            if r < 0.6 * self.threshold or now - self.open["t"] > 45:
                done, self.open = self.open, None
                self.last = now
                if done["z"] < self.min_z:
                    return None
                if done["reactions"] < self.min_reactions or done["reaction_share"] < self.min_reaction_share:
                    done["skipped"] = "chat was fast but not reacting (no LOL / CLIP IT / OMG...)"
                return done
        elif warm and r >= self.threshold and now - self.last >= self.cooldown_s:
            z, _ = self.z.z_now(messages, now)
            n, share = self.reactions(messages, now)
            self.open = {"t": now - self.window_s / 2, "rate": r, "z": z, "reactions": n, "reaction_share": share}
        return None

    def typical(self):
        if not self.rates:
            return 0.0
        xs = sorted(self.rates)
        return xs[len(xs) // 2]
