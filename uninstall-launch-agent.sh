#!/bin/zsh
set -euo pipefail

PLIST="$HOME/Library/LaunchAgents/com.airco-tracker.plist"
launchctl bootout "gui/$UID/com.airco-tracker" 2>/dev/null || true
rm -f "$PLIST"
print "Airco tracker schedule removed. Project data was kept."
