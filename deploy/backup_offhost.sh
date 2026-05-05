#!/usr/bin/env bash
# Off-host backup: nightly SQLite snapshot → rclone remote.
#
# Prerequisites:
#   1. rclone installed: sudo apt-get install rclone
#   2. rclone configured: rclone config  → add a remote named "backup"
#      Free options: Backblaze B2, Google Drive, MEGA, pCloud.
#      Example: rclone config → name="backup" type="b2"
#   3. RCLONE_REMOTE env var (default: backup:soul-coach-db)
#
# Schedule (crontab -e):
#   20 4 * * * /home/ubuntu/Bot_The_Soul_Coach/deploy/backup_offhost.sh
#
# The script keeps the last 14 daily snapshots on the remote and the last
# 7 local snapshots in ~/backups/.

set -euo pipefail

PROJECT="/home/ubuntu/Bot_The_Soul_Coach"
DB="$PROJECT/data/soul_coach.db"
LOCAL_DIR="$HOME/backups"
RCLONE_REMOTE="${RCLONE_REMOTE:-backup:soul-coach-db}"
LOG="$PROJECT/logs/backup.log"
KEEP_LOCAL=7
KEEP_REMOTE=14

mkdir -p "$LOCAL_DIR" "$(dirname "$LOG")"

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }
label=$(date -u +"%Y%m%d")
snapshot="$LOCAL_DIR/soul_coach.$label.db"

echo "[$(ts)] backup start → $snapshot" >> "$LOG"

# 1) Local snapshot via SQLite online backup (safe while DB is live).
if [ -f "$DB" ]; then
    sqlite3 "$DB" ".backup $snapshot"
    echo "[$(ts)] local snapshot OK ($(du -sh "$snapshot" | cut -f1))" >> "$LOG"
else
    echo "[$(ts)] WARN: DB not found at $DB" >> "$LOG"
    exit 1
fi

# 2) Upload to rclone remote.
if command -v rclone &>/dev/null; then
    rclone copy "$snapshot" "$RCLONE_REMOTE/" \
        --log-level INFO --log-file "$LOG" 2>&1 || \
        echo "[$(ts)] WARN: rclone upload failed" >> "$LOG"
    echo "[$(ts)] rclone upload done" >> "$LOG"

    # Prune remote: keep newest KEEP_REMOTE files.
    rclone delete "$RCLONE_REMOTE/" \
        --min-age "${KEEP_REMOTE}d" --log-level NOTICE --log-file "$LOG" 2>&1 || true
else
    echo "[$(ts)] WARN: rclone not installed — skipping off-host upload" >> "$LOG"
fi

# 3) Prune local backups older than KEEP_LOCAL days.
find "$LOCAL_DIR" -name 'soul_coach.*.db' -mtime +"$KEEP_LOCAL" -delete 2>/dev/null || true

echo "[$(ts)] backup done" >> "$LOG"
