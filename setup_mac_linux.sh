#!/bin/sh
# Schedules the dumbbell price tracker to run every hour (cron) on macOS or Linux.
# Run from this folder:   sh setup_mac_linux.sh
# Remove it later with:   crontab -l | grep -v 'tracker.py' | crontab -
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$(command -v python3)" || { echo "python3 not found — install Python 3 first."; exit 1; }
mkdir -p "$DIR/data"
LINE="17 * * * * cd \"$DIR\" && \"$PYTHON\" tracker.py >> data/tracker.log 2>&1"
( crontab -l 2>/dev/null | grep -v 'tracker.py' ; echo "$LINE" ) | crontab -
echo "Scheduled hourly check (at :17 past each hour). Log: $DIR/data/tracker.log"
echo "Running one check now..."
cd "$DIR" && "$PYTHON" tracker.py
