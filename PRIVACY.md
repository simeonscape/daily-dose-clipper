# Privacy policy: daily-dose-clipper

Last updated: 18 November 2026

daily-dose-clipper is a private automation tool used by one person to upload video clips to their own YouTube channel, Daily Dose of Streamers.

**What it accesses.** With the channel owner's permission, it uses the YouTube Data API to upload videos and post comments on that one channel. It does not read, collect or store data about any other YouTube user.

**What it stores.** It stores only an OAuth refresh token, kept as an encrypted GitHub Actions secret, and a list of the clips it has already posted (`state.json` in this repository). It does not sell, share or transfer any data.

**Revoking access.** The channel owner can revoke access at any time at https://myaccount.google.com/permissions.

**Contact.** Open an issue at https://github.com/simeonscape/daily-dose-clipper/issues.

This tool's use of information received from Google APIs follows the [YouTube API Services Terms of Service](https://www.youtube.com/t/terms) and the [Google Privacy Policy](https://policies.google.com/privacy).
