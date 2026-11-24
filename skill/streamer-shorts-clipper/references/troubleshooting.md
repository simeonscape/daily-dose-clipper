# Troubleshooting

Read the failing run's log first (Actions > the run > the failed step).

| Symptom | Cause and fix |
|---|---|
| `invalid_grant` / `Token has been expired or revoked` on upload or stats | The Google consent screen is still "Testing" (tokens die after 7 days) or access was revoked. Publish the consent screen, re-run `python setup_youtube.py`, update `YT_REFRESH_TOKEN`. |
| Uploads are private even with `privacy: public`; first comment fails | The API project isn't audited yet. Apply at https://support.google.com/youtube/contact/yt_api_form; make videos public by hand meanwhile. |
| `quotaExceeded` | More than about 6 uploads in a Pacific day. Lower `max_uploads_per_day` or the number of `post_times`. Deleted videos still count. |
| `401` / `invalid client` from Twitch | Wrong `TWITCH_CLIENT_ID` / `TWITCH_CLIENT_SECRET`, or the secret was regenerated. |
| "No enabled streamer with login X" | The login isn't in `config.yaml`, or `enabled: false`. |
| "Nothing usable for X" | No recent clips, VODs off or subscriber-only, or every candidate already posted or too explicit. Check `check_setup.py` with Twitch keys for "has VODs". |
| Save state step fails to push | Actions can't write to `main`: Settings > Actions > General > Workflow permissions > Read and write, and let Actions bypass branch protection. |
| Live watcher never triggers | Real chat speeds are below `live.chat_per_1000`. Read the per-minute log lines (now / normal / peak per 1,000) and set the threshold a bit under typical peaks. |
| Live watcher uses up Actions minutes | Private repos get 2,000 free minutes a month; the watcher needs ~43,000. Make the repo public or set `live.enabled: false`. |
| Facecam box wrong | Look at `<clip>_camera.jpg` in Artifacts and set `cam: [x, y, w, h]` (fractions) or `layout:` for that streamer. |
| Too much / too little blurred | Look at `<clip>_blurred.jpg`; raise or lower `safety.threshold`. |
| Subtitles off | Use `whisper_model: medium` (slower). |
| Scheduled runs stopped | GitHub pauses schedules after 60 days without commits; the bot's state commits normally prevent this. Re-enable the workflow under Actions. |
| `claude` writer failed | The `CLAUDE_CODE_OAUTH_TOKEN` expired or the plan's limit was hit; the built-in writer takes over automatically. Run `claude setup-token` again to refresh. |
