---
name: streamer-shorts-clipper
description: Set up, run or tune an automatic Twitch-to-YouTube Shorts clip channel on GitHub Actions (viral moment picking, branded vertical edit, subtitles, scheduled and live posting, channel stats and summaries).
---

# Streamer Shorts clipper

A complete, hands-off clip channel. It picks each streamer's most viral Twitch moment, cuts it, edits it into a branded 1080x1920 Short (layout picked per clip, word-by-word subtitles, 18+ blur), writes the title, description, hashtags and first comment, and uploads it to YouTube at set times. An always-on watcher catches moments live. Everything runs free on GitHub Actions; the user's computer is only needed once, to sign in to YouTube.

## What's in this skill

| Path | What it is |
|---|---|
| `template/` | The whole engine as a ready repo: `run.py`, `slots.py`, `watcher.py`, `stats.py`, `statemerge.py`, `setup_youtube.py`, `engine/`, `assets/` (fonts, face model), `config.yaml`, and 4 workflows in `.github/workflows/` (Daily clips, Clip now, Live watcher, Channel stats). |
| `templates/` | README, PRIVACY and TERMS with `{{placeholders}}` (Google's API audit needs the last two). |
| `scripts/configure.py` | Copies `template/` into a new folder and fills in channel, colour, streamers, times, docs and a placeholder logo. Re-run it to change those. |
| `scripts/streamer_pics.py` | Downloads each streamer's Twitch picture into `assets/streamers/<login>.png`. |
| `scripts/check_setup.py` | Checks a configured repo (and the Twitch logins, when Twitch keys are in the environment). |
| `scripts/set_secrets.sh` | Saves the six GitHub secrets with the `gh` CLI, typed in hidden. |
| `scripts/brand_pfp.py`, `scripts/banner.py` | Channel art: cut-out profile picture and 2560x1440 banner in the brand colour. |
| `references/` | `settings.md` (every config.yaml knob), `notifications.md` (alerts and summaries), `troubleshooting.md`. |

**Finding the bundle.** Set `SKILL_DIR` to the folder holding this SKILL.md. If `SKILL_DIR/template/run.py` is missing (the skill was saved as instructions only), download the bundle into a new empty folder and use that as `SKILL_DIR`:

```bash
mkdir -p ~/.cache/streamer-shorts-clipper && cd ~/.cache/streamer-shorts-clipper
curl -sSL https://codeload.github.com/simeonscape/daily-dose-clipper/tar.gz/main \
  | tar -xz --strip-components=3 daily-dose-clipper-main/skill/streamer-shorts-clipper
```

## Rules

- **Never ask the user to paste a secret into the chat**, and never write one to a file, a commit, memory or a log. Secrets go straight into GitHub: the user runs `scripts/set_secrets.sh`, or types them in **Settings > Secrets and variables > Actions**. If a user pastes one anyway, don't repeat it, and suggest they rotate it.
- The user's own accounts can't be created for them: a GitHub account, a Twitch developer app, a Google Cloud project and YouTube channel, and (optionally) a Claude Pro/Max plan for AI-written titles. Walk them through each with the exact links below.
- Before the first real upload, do a dry run and have the user watch the video. Keep `live.post: false` until they've checked a live dry run too.
- Copyright: clips are the streamers' content. Tell the user once, plainly, to join streamers' clip programs or get permission; three strikes deletes a channel.
- Run Python that reads downloaded or user files with `python -I` where you can.

## Setup, step by step

Track these as a checklist and do the parts you can do yourself.

### 1. Ask for the basics (one message)
- Channel name (as on YouTube), and a brand colour (hex; default `#D97757`).
- Streamers: display name and Twitch login for each (`twitch.tv/<login>`). Up to about 6, since YouTube's free quota allows about 6 uploads a day.
- Posting times and time zone (default `12:00, 15:00, 17:00, 19:00, 21:00` America/New_York). One time per streamer means each gets a Short every day.
- Their name and a contact email or URL (shown on the Privacy and Terms pages Google reviews).
- GitHub `owner/repo` for the new repo. Recommend **public**: the always-on live watcher uses about 43,000 Actions minutes a month, which is free only on public repos (private repos get 2,000). Secrets stay hidden either way. If they want private, turn the watcher off (`live.enabled: false`).
- Uploads `public` or `private` (Google forces private until the API audit anyway).

### 2. Build the repo
```bash
python3 -m pip install pyyaml pillow requests
python3 -I "$SKILL_DIR/scripts/configure.py" --out ./my-clipper \
  --channel "CHANNEL" --streamers "Name=login,Name2=login2" \
  --owner "NAME" --contact "EMAIL_OR_URL" --repo "owner/repo" \
  --color "#D97757" --timezone America/New_York --times "12:00,15:00,17:00,19:00,21:00"
python3 -I "$SKILL_DIR/scripts/check_setup.py" ./my-clipper
```
Put the channel's square icon at `assets/logo.png` if they have one (else the placeholder with initials stays). Streamer pictures: after step 4, run `TWITCH_CLIENT_ID=... TWITCH_CLIENT_SECRET=... python3 -I "$SKILL_DIR/scripts/streamer_pics.py" ./my-clipper` on the user's machine, or let them drop 240x240 PNGs into `assets/streamers/<login>.png`. Without a picture the badge shows just the name.

### 3. Push it to GitHub
Create the repo and push the folder: with GitHub tools if you have them, with `gh repo create owner/repo --public --source ./my-clipper --push`, or by asking the user to create an empty repo and running `git init && git add -A && git commit -m "Clipper" && git branch -M main && git remote add origin https://github.com/owner/repo.git && git push -u origin main`. The workflows commit `state.json`, `channel.json` and `history.json` back to `main`, so `main` must accept pushes from Actions: in **Settings > Actions > General > Workflow permissions** choose **Read and write permissions**. If `main` is protected, allow GitHub Actions to bypass.

### 4. Twitch keys (user, 2 minutes)
https://dev.twitch.tv/console/apps > **Register Your Application**: any name, OAuth redirect `http://localhost`, category **Application Integration**. Copy the **Client ID** and create a **Client Secret**.

### 5. YouTube upload access (user, about 15 minutes)
1. https://console.cloud.google.com/ > new project > enable **YouTube Data API v3**.
2. **OAuth consent screen**: External; add their Google account as a test user; then **Publish app** ("In production"), or the refresh token dies after 7 days.
3. **Credentials > Create OAuth client ID > Desktop app**, download as `client_secret.json`.
4. On their computer, in the repo folder: `pip install google-auth-oauthlib` then `python setup_youtube.py`. They sign in and pick the channel; it prints `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`. (`client_secret.json` is git-ignored; never commit it.)
5. Apply for the YouTube API audit at https://support.google.com/youtube/contact/yt_api_form, linking the repo's PRIVACY.md and TERMS.md. Until approved, uploads are forced private and the first comment can't be posted.

### 6. Optional: Claude writes titles and judges clips
On any computer with Claude Code: `claude setup-token` (needs Pro or Max). The printed token is the `CLAUDE_CODE_OAUTH_TOKEN` secret. Without it a built-in writer and judge are used, at no cost.

### 7. Secrets (user)
`bash "$SKILL_DIR/scripts/set_secrets.sh" owner/repo`, or by hand under **Settings > Secrets and variables > Actions**:
`TWITCH_CLIENT_ID`, `TWITCH_CLIENT_SECRET`, `YT_CLIENT_ID`, `YT_CLIENT_SECRET`, `YT_REFRESH_TOKEN`, and optionally `CLAUDE_CODE_OAUTH_TOKEN`.

### 8. Dry run, then go live
- **Actions > Daily clips > Run workflow**, "dry run" ticked. When it finishes, the user downloads the video from the run's **Artifacts** (`shorts`) and checks it, along with `<clip>_camera.jpg` (facecam found) and `<clip>_blurred.jpg` (18+ blur).
- If you can read run logs, check them for errors and for "Made N of N Short(s)".
- Once it looks right, nothing else is needed: the hourly **Daily clips** schedule posts one Short at each posting time, the **Live watcher** runs 24/7, and **Channel stats** updates `channel.json` every 30 minutes. After the user has checked a **Clip now** video from a live moment, set `live.post: true`.

### 9. Optional extras
- Alerts and daily/weekly/monthly summaries: `references/notifications.md`.
- Channel art: `scripts/brand_pfp.py` (needs `rembg[cpu] scipy numpy`) and `scripts/banner.py`.

## Running it day to day

- **Change streamers, times, colour or name**: re-run `configure.py` with the same `--out` (it updates config.yaml, the workflow's streamer menu and the docs; `--color`, `--times`, `--timezone` and `--privacy` left out keep their current values, and `--streamers` replaces the whole list), or edit `config.yaml` by hand. If you edit the streamer list by hand, also update the `streamer` options in `.github/workflows/daily.yml`, then run `check_setup.py`.
- **Tune**: every knob is in `config.yaml`, explained in `references/settings.md`. Common ones: `live.chat_per_1000` (read real numbers from the Live watcher log first), `privacy`, `layout`, `safety.threshold`, `whisper_model`, per-streamer `cam: [x, y, w, h]` or `layout:`.
- **Something broke**: `references/troubleshooting.md`.

## How it works (for answering questions)

- **Picking**: candidates are each streamer's most-viewed Twitch clips of the last `clip_days` (clips of one moment are merged, live streams included), else chat spikes in the latest VOD. Each gets a virality score (views, number of clips, chat explosion, laughter, CLIP IT spam, recency; weights under `virality:`). A moment already posted (same VOD time, clip id or title) is skipped.
- **Smart cut**: downloads `smart.context_before`/`after` seconds around it and judges the real setup, payoff and reaction from transcript, chat, loudness, frames and viewer clips (Claude when the token is set, else built-in), then cuts 20 to 60 seconds starting at a sentence.
- **Edit**: NudeNet blurs explicit spots only while on screen (mostly explicit clips are skipped); faster-whisper subtitles; layout `split`, `pip`, `cam` or `game` chosen per clip with a crop that follows the action; loudness levelled to -14 LUFS.
- **Posting**: `slots.py` finds a posting time within the next 80 minutes; the run makes the Short, waits, and uploads on time. One Short per streamer per day (live or scheduled), at most `max_uploads_per_day` (quota resets at midnight Pacific; deleted videos still count). `state.json` records posts and is merged by `statemerge.py` so workflows never overwrite each other.
- **Live**: `watcher.py` reads chats anonymously over IRC every second; over `live.chat_per_1000` messages/min per 1,000 viewers and `min_chat_z` above the stream's own normal, it dispatches **Clip now**, which cuts from the live VOD. Each run hands over to a fresh run near 5.75 h, so there's no gap; a 15-minute cron restarts it after a crash.
- **Stats**: `stats.py` writes `channel.json` (live numbers, today's Shorts) and `history.json` (one snapshot a day per Short), read-only, about 3 quota units a run.
