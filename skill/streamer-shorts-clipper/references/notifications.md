# Alerts and channel summaries

The engine itself sends nothing anywhere: every result is in GitHub (run logs,
Artifacts, `state.json`, `channel.json`, `history.json`). Alerts and summaries
are best done as **scheduled Claude tasks** that read those files and post in a
chat or project thread the user follows. Nothing extra to install, and no
phone number or third-party app is needed. Ask the user which time zone to
word times in.

## Data the tasks read (all on `main`)
- `channel.json`: updated every 30 min. `channel` (subscribers, views, videos),
  `today_videos` and `recent` (id, title, url, views, likes, comments, streamer, day).
- `history.json`: `days[YYYY-MM-DD]` = one snapshot per posting day (channel
  totals + `per_video[id] = [views, likes, comments]`), and `videos[id]` = a
  catalog of every Short ever seen. Days use `post_timezone`.
- `state.json`: `posted[]` (streamer, video_id, at, slot, layout, title).
- Workflow runs of `clip-now.yml` (each is a live moment) and `daily.yml`.

## Suggested tasks
1. **Clip now alerts**, hourly: list finished `clip-now.yml` runs since the last
   check. For each new one, post the streamer, the chat speed (in the run name),
   whether it posted (link `https://youtube.com/shorts/<id>` from state.json) or
   only made a video (dry run / `live.post: false`), or why it failed (job log).
   Keep the reported run ids in memory so nothing is repeated.
2. **Daily summary**, morning: yesterday's Shorts with views/likes/comments,
   subscriber and view change (compare the last two `history.json` days), the
   best Short, any failed `daily.yml` runs.
3. **Weekly summary** (last 7 days) and **monthly summary** (on the 1st): totals,
   growth, top 3 Shorts, best streamer and best posting time by average views.

Create them with whatever scheduler the user's Claude app offers (scheduled
tasks / triggers), each with a prompt that names the repo, the files above
and the thread to post in.

If the user isn't using Claude for this: GitHub already emails the repo owner
when a workflow fails (Settings > Notifications > Actions).
