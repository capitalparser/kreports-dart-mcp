#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${KREPORTS_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LABEL="${KREPORTS_LAUNCHD_LABEL:-com.kjun.kreports-dart-backfill}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
ENV_FILE="${KREPORTS_COLLECTOR_ENV:-$HOME/.config/kreports/collector.env}"

mkdir -p "$HOME/Library/LaunchAgents" "$(dirname "$ENV_FILE")"

if [[ ! -f "$ENV_FILE" ]]; then
  cat > "$ENV_FILE" <<'ENV'
# Private collector environment. Keep this file chmod 600.
# DART_API_KEY=put_your_key_here
KREPORTS_RUNTIME_MODE=collector
ENV
  chmod 600 "$ENV_FILE"
  echo "Created collector env template: $ENV_FILE"
  echo "Edit it and set DART_API_KEY before expecting backfill to run."
fi

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$PROJECT_DIR/scripts/dart_limit_aware_backfill.sh</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$PROJECT_DIR</string>
  <key>StartCalendarInterval</key>
  <array>
    <dict><key>Hour</key><integer>0</integer><key>Minute</key><integer>20</integer></dict>
    <dict><key>Hour</key><integer>6</integer><key>Minute</key><integer>20</integer></dict>
    <dict><key>Hour</key><integer>12</integer><key>Minute</key><integer>20</integer></dict>
    <dict><key>Hour</key><integer>18</integer><key>Minute</key><integer>20</integer></dict>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>StandardOutPath</key>
  <string>$PROJECT_DIR/logs/launchd-dart-backfill.out.log</string>
  <key>StandardErrorPath</key>
  <string>$PROJECT_DIR/logs/launchd-dart-backfill.err.log</string>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl enable "gui/$(id -u)/$LABEL"

echo "Installed launchd job: $LABEL"
echo "Plist: $PLIST"
echo "Collector env: $ENV_FILE"
echo "Status:"
launchctl print "gui/$(id -u)/$LABEL" | sed -n '1,80p'
