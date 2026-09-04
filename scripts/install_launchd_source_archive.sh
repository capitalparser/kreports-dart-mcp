#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${KREPORTS_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LABEL="${KREPORTS_SOURCE_ARCHIVE_LAUNCHD_LABEL:-com.kjun.kreports-source-archive}"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
COLLECTOR_ENV="${KREPORTS_SOURCE_ARCHIVE_COLLECTOR_ENV:-$PROJECT_DIR/.env.collector}"
DRIVE_ENV="${KREPORTS_SOURCE_ARCHIVE_DRIVE_ENV:-$PROJECT_DIR/.env.drive}"

for env_file in "$COLLECTOR_ENV" "$DRIVE_ENV"; do
  if [[ ! -f "$env_file" ]]; then
    echo "missing private environment file: $env_file" >&2
    exit 64
  fi
  if [[ "$env_file" == *'<'* || "$env_file" == *'>'* || "$env_file" == *'&'* ]]; then
    echo "environment path contains unsupported plist characters: $env_file" >&2
    exit 64
  fi
done

mkdir -p "$HOME/Library/LaunchAgents" "$PROJECT_DIR/logs"

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$PROJECT_DIR/scripts/source_archive_auto_backfill.sh</string>
  </array>
  <key>WorkingDirectory</key><string>$PROJECT_DIR</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>KREPORTS_SOURCE_ARCHIVE_COLLECTOR_ENV</key><string>$COLLECTOR_ENV</string>
    <key>KREPORTS_SOURCE_ARCHIVE_DRIVE_ENV</key><string>$DRIVE_ENV</string>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>900</integer>
  <key>KeepAlive</key>
  <dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>30</integer>
  <key>StandardOutPath</key><string>$PROJECT_DIR/logs/source-archive-auto.out.log</string>
  <key>StandardErrorPath</key><string>$PROJECT_DIR/logs/source-archive-auto.err.log</string>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)" "$PLIST" >/dev/null 2>&1 || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl enable "gui/$(id -u)/$LABEL"
echo "Installed launchd job: $LABEL"
echo "Collector env: $COLLECTOR_ENV"
echo "Drive env: $DRIVE_ENV"
launchctl print "gui/$(id -u)/$LABEL" | sed -n '1,80p'
