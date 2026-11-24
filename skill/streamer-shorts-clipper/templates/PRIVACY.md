# Privacy Policy: {{CHANNEL}} clipper

Last updated: {{DATE}}

{{CHANNEL}} clipper is a private automation tool used by one person to upload video clips to their own YouTube channel, {{CHANNEL}}. It uses **YouTube API Services**.

## YouTube API Services
- This tool uses YouTube API Services to upload videos and post comments on the channel owner's own channel.
- By using this tool, you agree to be bound by the [YouTube Terms of Service](https://www.youtube.com/t/terms).
- Google's privacy practices are described in the [Google Privacy Policy](https://policies.google.com/privacy).

## What data it accesses
With the channel owner's permission (Google OAuth 2.0), it uses the YouTube Data API to:
- upload videos to the owner's channel (`videos.insert`), and
- post one comment on each of those videos (`commentThreads.insert`).

It does not read, collect, store or share data about any other YouTube user, and it does not display YouTube data to anyone.

## What data it stores
- An OAuth refresh token, stored as an encrypted GitHub Actions secret in the owner's repository.
- The IDs of videos it has uploaded, in `state.json`, so the same clip isn't posted twice.

No data is sold, shared, or transferred to anyone.

## Deleting your data and revoking access
- Revoke the tool's access at any time at https://myaccount.google.com/permissions or https://security.google.com/settings/security/permissions. After that it can no longer access the channel.
- To delete stored data, remove the `YT_REFRESH_TOKEN` secret and clear `state.json` in the repository. Stored data is deleted within 7 days of a request, and the refresh token is never kept for longer than 30 days after access is revoked.

## Contact
Open an issue at https://github.com/{{REPO}}/issues or email {{CONTACT}}.
