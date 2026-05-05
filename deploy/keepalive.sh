#!/usr/bin/env bash
# Keep CPU above Oracle's idle-reclamation threshold.
#
# Oracle reclaims Always Free instances when 95th-percentile CPU drops below
# 20% over 7 consecutive days. We run 60s of light CPU work every 5 minutes
# (~20% utilization rate), comfortably above the threshold.
#
# Also performs light DB housekeeping so the work isn't entirely wasted.

set -euo pipefail

PROJECT="/home/ubuntu/Bot_The_Soul_Coach"
DB="$PROJECT/data/soul_coach.db"
LOG="$PROJECT/logs/keepalive.log"

mkdir -p "$(dirname "$LOG")"

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

echo "[$(ts)] keepalive tick start" >> "$LOG"

# 1) DB housekeeping: WAL checkpoint + integrity quick check.
if [ -f "$DB" ]; then
    sqlite3 "$DB" "PRAGMA wal_checkpoint(TRUNCATE); PRAGMA quick_check;" \
        >> "$LOG" 2>&1 || true
fi

# 2) Burn ~60s of CPU. Single core (Ampere A1 has 2 OCPU; we use one for
#    ~50% CPU during this window, plenty to keep 95p above 20%).
end=$(( $(date +%s) + 60 ))
sum=0
i=0
while [ "$(date +%s)" -lt "$end" ]; do
    sum=$(( sum + i ))
    i=$(( i + 1 ))
    [ $(( i % 100000 )) -eq 0 ] && sum=$(( sum % 1000003 ))
done

echo "[$(ts)] keepalive tick done sum=$sum" >> "$LOG"
