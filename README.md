# Daily Dose of Streamers clipper

[Privacy Policy](PRIVACY.md) · [Terms of Service](TERMS.md) · Uses **YouTube API Services**. By using this tool you agree to the [YouTube Terms of Service](https://www.youtube.com/t/terms) and the [Google Privacy Policy](https://policies.google.com/privacy).

Posts each streamer's most viral moment to the Daily Dose of Streamers channel as branded Shorts. It runs five times a day, and you can start a run by hand that makes 1, 2, 3 or 5 Shorts (one per streamer).

**How it picks a clip:** one Short per streamer, and it goes for the most viral moment, not just any spike. Candidates are the streamer's most-viewed Twitch clips from the last 7 days (clips of the same moment are added together), including clips from a stream that is live right now. If there are none, it uses chat spikes in their latest stream. The top few candidates get the chat around them downloaded, and each gets one virality score from clip views, how many people clipped it, how hard chat exploded, how much of chat was laughing (KEKW, 💀, LMAO), CLIP IT spam and how recent it is (weights under `virality:` in `config.yaml`). Then the **smart clipper** takes over: a clip or a chat spike only says roughly where the moment is, and the real highlight can be 30+ seconds earlier or later. So it downloads about 75 seconds before and 60 after, and judges what actually happened from the transcript, chat, loudness, frames and the viewers' own clips. It then cuts from the setup through the payoff to the end of the reaction (20 to 60 seconds, starting at the beginning of a sentence). With `CLAUDE_CODE_OAUTH_TOKEN` set, Claude does the judging and looks at the frames too; otherwise a built-in judge does. Settings are under `smart:`.

**18+ content:** every clip is scanned with NudeNet, a small free model that runs on GitHub with no API key. Only the spots it finds (nude characters in GTA, for example) are blurred, and only while they're on screen. A clip that's explicit for most of its length is skipped and the next-best moment is used. Each run saves `<clip>_blurred.jpg` showing what was blurred. Settings are under `safety:`.

**How it edits:** a 1080x1920 vertical video with the orange brand background, the streamer's picture and name at the top, a hook in big text, and word-by-word subtitles. The layout is picked per clip: **split** (facecam on top, whole screen below) when the stream has a good facecam, **pip** (gameplay big with a small facecam box in the corner) when the facecam is small or the moment is all about the game, **cam** (full camera) for Just Chatting, and **game** when there's no camera. In the single-panel layouts the crop follows the action or the streamer's face, smoothly. Audio is levelled to YouTube loudness.

**Live streams:** the **Live watcher** workflow is always on, 24/7, with nothing to fill in. It reads the five streamers' live chats and measures chat speed per 1,000 viewers (messages a minute ÷ viewers/1000). When it goes over `live.chat_per_1000` (100 by default) and is well above that stream's own normal, it starts **Clip now** for that moment, which cuts it from the stream's live VOD, edits it and posts it, usually within about 10 minutes. **Each streamer gets one Short a day:** once they have one, live or scheduled, they're checked off, and the scheduled times only fill in for streamers who didn't get a live one. Every 10 minutes the watcher's log shows each stream's normal and peak chat speed per 1,000 viewers, so you can tune the threshold. With `live.post: false` Clip now only makes the video (download it from the run's Artifacts). A streamer with VODs turned off can't be clipped live.

**Phone alerts:** to get a push notification each time Clip now finishes, install the free **ntfy** app (iPhone or Android), tap **+**, subscribe to a topic name that's hard to guess (for example `ddc-clips-` plus random letters, since anyone who knows the name can read it), and save that name as the secret `NTFY_TOPIC`. You'll get what happened in the clip, the chat speed, and either the YouTube link or a note that it's waiting in the run's Artifacts. Tapping the alert opens the run. Without the secret, nothing is sent.

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
| `NTFY_TOPIC` (optional) | your own topic name in the free ntfy app, see below |

### 6. Test, then go live
- Under **Actions > Daily clips > Run workflow**, pick how many Shorts (1, 2, 3 or 5) and which streamer (or all), and leave "dry run" ticked. When it finishes, download the video from the run's **Artifacts** and check it.
- Once it looks right, the schedule takes over: one Short at each of the `post_times` in `config.yaml` (12:00, 15:00, 17:00, 19:00 and 21:00 US Eastern), one streamer each, so all five get a Short every day, spread out. The workflow checks every hour; about an hour before a posting time it makes the Short, waits, and posts it on time. It never uploads more than 6 a day, which is YouTube's free quota, and never posts a moment that was already posted.

## Settings (`config.yaml`)
- `privacy: private` makes uploads private so you can check each one first.
- Layout is automatic (see above). Each run saves `<clip>_camera.jpg` in the download showing the facecam it found. If it's wrong for a streamer, set `cam: [x, y, width, height]` (fractions of the screen) for them, and `layout: split` (or pip / cam / game) to force a layout.
- `safety.threshold`: lower blurs more, with more false alarms. `safety.enabled: false` turns the scan off.
- `live.post: true` lets Clip now upload. `live.chat_per_1000` sets how fast chat must go to count as viral.
- `whisper_model`: `small` is a good balance. `medium` gives better subtitles but runs slower.
- Each streamer's Twitch `login`. Streamers who mostly stream on YouTube or Kick are turned off (`enabled: false`) because TwitchDownloader only supports Twitch.

## Limits to know
- **Copyright:** clips are the streamers' content. Unpermitted clips can get claims or strikes, and three strikes deletes the channel. Join their clip programs or ask permission (see the launch plan).
- **YouTube quota:** 10,000 units a day. An upload costs 1,600 and a comment 50, so 5 a day fits and 6 is the most.
- **Live clipping** needs the stream to be public on Twitch. Live moments are cut from the stream's live VOD, so a streamer needs VODs turned on.
- **Costs:** GitHub Actions is free for public repos. Private repos get 2,000 free minutes a month, and each run takes about 10 to 20 minutes, so 5 runs a day may go over. Titles cost nothing with the built-in writer. With the subscription option, they count toward your Claude plan's usage.

## Channel stats for School OS
The **Channel stats** workflow runs every 30 minutes. It reads the channel's subscribers, views and the Shorts posted today from YouTube (read-only, about 3 of the 10,000 daily quota units) with the same three `YT_` secrets, and saves them to `channel.json`. Apps read it from
`https://raw.githubusercontent.com/simeonscape/daily-dose-clipper/main/channel.json`, so no API key ever goes into an app. `state.json` next to it says which streamer each Short is.
