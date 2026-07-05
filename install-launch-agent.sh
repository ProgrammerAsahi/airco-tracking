#!/bin/zsh
set -euo pipefail

PROJECT_DIR="${0:A:h}"
PLIST="$HOME/Library/LaunchAgents/com.airco-tracker.plist"

if [[ ! -f "$PROJECT_DIR/.env" ]]; then
  print -u2 "Missing $PROJECT_DIR/.env — copy .env.example and fill in email settings first."
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents"
sed "s|__PROJECT_DIR__|$PROJECT_DIR|g" "$PROJECT_DIR/com.airco-tracker.plist.example" > "$PLIST"
launchctl bootout "gui/$UID/com.airco-tracker" 2>/dev/null || true
launchctl bootstrap "gui/$UID" "$PLIST"
launchctl kickstart -k "gui/$UID/com.airco-tracker"
print "Installed. The tracker now runs every 10 minutes."
print "Logs: $PROJECT_DIR/tracker.log and $PROJECT_DIR/tracker.err.log"
