# Daily Dose of Streamers clipper

[Privacy Policy](PRIVACY.md) · [Terms of Service](TERMS.md) · Uses **YouTube API Services**. By using this tool you agree to the [YouTube Terms of Service](https://www.youtube.com/t/terms) and the [Google Privacy Policy](https://policies.google.com/privacy).

Posts one branded Short to the Daily Dose of Streamers channel per run, five runs a day.

**How it picks a clip:** it downloads the chat of the streamer's latest finished Twitch stream (VOD) with [TwitchDownloader](https://github.com/lay295/TwitchDownloader). It finds the moments where chat spikes (lots of KEKW, OMEGALUL, CLIP IT, 💀) compared with normal chat around them, and takes the biggest spike it hasn't posted yet. Then it downloads 30 seconds before and 30 seconds after that moment.

**How it edits:** it makes a 1080x1920 vertical video with an orange brand background, the channel chip at the top, a hook in big text, the stream inside a white frame, and word-by-word subtitles. Audio is levelled to YouTube loudness.

**How it posts:** it writes the hook, title, description, hashtags and a first comment, then uploads the clip and posts the comment. By default a built-in writer does this with no API key: it reads what chat was spamming to tell what kind of moment it was (funny, hype, shocking, a fail or rage) and fills tested Shorts patterns, using a punchy line from the transcript when there is one. You can also let your Claude subscription write them instead (step 3). The clip is uploaded to YouTube and the comment is posted. YouTube's API can't pin comments, so pin it in YouTube Studio if you want.

## Setup (about 30 minutes, once)

### 1. GitHub repo
Create a **private** repo (for example `daily-dose-clipper`) and put these files in it.

### 2. Twitch keys (to find the latest streams)
Go to https://dev.twitch.tv/console/apps, then **Register Your Application**. Use name `daily-dose-clipper`, OAuth redirect `http://localhost`, and category **Application Integration**. Copy the **Client ID**, then make a **Client Secret**.

### 3. Optional: let your Claude subscription write the titles
You can skip this step: the built-in writer needs no key. If you'd like Claude to write each title and hook from the actual clip, install Claude Code on any computer (https://claude.com/claude-code), run `claude setup-token`, and save the token it prints as the secret `CLAUDE_CODE_OAUTH_TOKEN`. This uses your Claude plan (Pro or Max), with no separate API bill. If it ever fails, the built-in writer takes over.

### 4. YouTube upload access
1. Go to https://console.cloud.google.com/, create a project, and enable **YouTube Data API v3**.
2. Under **OAuth consent screen**, choose External and add your Google account as a test user.
3. Under **Credentials**, create an **OAuth client ID** of type **Desktop app** and download it as `client_secret.json`.
4. On any computer with Python: `pip install google-auth-oauthlib`, then run `python setup_youtube.py`. Sign in and pick the Daily Dose of Streamers channel. It prints three values.
5. **Important:** Google locks videos uploaded through a new, unaudited API project to **private**. Apply for the audit at https://support.google.com/youtube/contact/yt_api_form so uploads can be public. Until it's approved, the engine works, but you have to make each video public yourself in YouTube Studio.
6. While the consent screen is in "Testing", the refresh token expires after 7 days. Set it to "In production" to avoid that.

### 5. Add the secrets
In the repo, go to **Settings > Secrets and variables > Actions > New repository secret** and add:

| Secret | From |
|---|---|
| `TWITCH_CLIENT_ID` | step 2 |
| `TWITCH_CLIENT_SECRET` | step 2 |
| `CLAUDE_CODE_OAUTH_TOKEN` (optional) | step 3 |
| `YT_CLIENT_ID` | step 4 |
| `YT_CLIENT_SECRET` | step 4 |
| `YT_REFRESH_TOKEN` | step 4 |

### 6. Test, then go live
- Under **Actions > Daily clips > Run workflow**, leave "dry run" ticked. When it finishes, download the video from the run's **Artifacts** and check it.
- Once it looks right, the schedule takes over: 5 runs a day (times in `.github/workflows/daily.yml`, in UTC), one Short each, rotating through the streamers.

## Settings (`config.yaml`)
- `privacy: private` makes uploads private so you can check each one first.
- `crop`: `center43` (default) suits face-cam streams. Use `full` for gameplay with a facecam corner, or `square`.
- `whisper_model`: `small` is a good balance. `medium` gives better subtitles but runs slower.
- Each streamer's Twitch `login`. Streamers who mostly stream on YouTube or Kick are turned off (`enabled: false`) because TwitchDownloader only supports Twitch.

## Limits to know
- **Copyright:** clips are the streamers' content. Unpermitted clips can get claims or strikes, and three strikes deletes the channel. Join their clip programs or ask permission (see the launch plan).
- **YouTube quota:** 10,000 units a day. An upload costs 1,600 and a comment 50, so 5 a day fits and 6 is the most.
- **Runs after the stream, not live:** clips come from the last finished VOD, usually a few hours after the stream ends. This is more reliable than live capture, and it needs no computer of yours.
- **Costs:** GitHub Actions is free for public repos. Private repos get 2,000 free minutes a month, and each run takes about 10 to 20 minutes, so 5 runs a day may go over. Titles cost nothing with the built-in writer. With the subscription option, they count toward your Claude plan's usage.
