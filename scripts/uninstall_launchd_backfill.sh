#!/usr/bin/env bash
set -euo pipefail

LABEL="${KREPORTS_LAUNCHD_LABEL:-com.kjun.kreports-dart-backfill}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
rm -f "$PLIST"
echo "Uninstalled launchd job: $LABEL"
