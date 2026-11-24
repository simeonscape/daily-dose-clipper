#!/usr/bin/env bash
# Save the clipper's keys as GitHub Actions secrets with the GitHub CLI.
#   bash scripts/set_secrets.sh owner/repo
# Needs `gh` signed in (gh auth login). Each value is typed in hidden and
# goes straight to GitHub; nothing is written to disk. Leave a value empty
# to skip it (CLAUDE_CODE_OAUTH_TOKEN is optional).
set -euo pipefail
REPO="${1:?usage: set_secrets.sh owner/repo}"
command -v gh >/dev/null || { echo "Install the GitHub CLI first: https://cli.github.com"; exit 1; }
for NAME in TWITCH_CLIENT_ID TWITCH_CLIENT_SECRET YT_CLIENT_ID YT_CLIENT_SECRET YT_REFRESH_TOKEN CLAUDE_CODE_OAUTH_TOKEN; do
  read -r -s -p "$NAME (Enter to skip): " VALUE; echo
  if [ -n "$VALUE" ]; then
    printf '%s' "$VALUE" | gh secret set "$NAME" -R "$REPO" --body -
    echo "  saved $NAME"
  fi
done
echo "Secrets now on $REPO:"; gh secret list -R "$REPO"
