# Deploy on Oracle Cloud Always Free

End-to-end playbook. Targets the **Ampere A1 Flex** Always-Free shape
(2 OCPU / 12 GB RAM Ubuntu 22.04). Covers VM provisioning, hardening,
Python deploy, systemd unit, anti-idle keepalive, and ongoing operations.

> **Charging guarantee.** Stay on the Always Free account. Do **not** click
> "Upgrade to Pay-As-You-Go" anywhere in the console. Set a $0.01 budget
> alert as a tripwire (Settings → Budgets). Only Always-Free-eligible
> resources can be created on a non-upgraded account; attempts to create
> paid resources fail rather than charge.

> **Idle-reclamation guarantee.** The `keepalive.timer` (5-min cadence,
> 60s CPU each tick) targets ~20% utilization rate, which keeps the
> 95th-percentile CPU comfortably above Oracle's 20% reclamation
> threshold. Verify with `top` after first day of running.

---

## 1. Create the VM

1. Sign up at https://www.oracle.com/cloud/free/. You'll get a 30-day $300 trial AND Always-Free resources. After 30 days the account stays Always-Free unless you choose to upgrade — **don't**.
2. In the console: **Compute → Instances → Create Instance**.
3. Image: **Ubuntu 22.04**. Shape: change to **Ampere — VM.Standard.A1.Flex**, 2 OCPU / 12 GB RAM (this is in the Always-Free allowance).
4. Networking: keep the default VCN. Enable **Assign a public IPv4 address**.
5. SSH keys: paste your public key.
6. Boot volume: leave at default (50 GB Always-Free).
7. **Create**. Wait ~2 min for it to come up.

If you get "Out of capacity" on A1 (common in some regions): retry every few hours, or temporarily fall back to two **VM.Standard.E2.1.Micro** AMD shapes (also Always-Free, 1/8 OCPU + 1 GB RAM each). The bot fits in the AMD shape but RAM headroom is tight.

## 2. SSH + base setup

```bash
ssh ubuntu@<public-ip>

# System update
sudo apt-get update && sudo apt-get upgrade -y

# Open the firewall (Oracle iptables blocks all by default)
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 22 -j ACCEPT
sudo netfilter-persistent save

# Optional: disable password login
sudo sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config
sudo systemctl restart ssh

# Install dependencies
sudo apt-get install -y python3 python3-venv python3-pip git sqlite3
```

## 3. Get the code onto the VM

Option A — clone from your git repo (recommended):

```bash
cd ~
git clone <your-git-url> Bot_The_Soul_Coach
cd Bot_The_Soul_Coach
```

Option B — `scp` from your laptop:

```bash
# from laptop
scp -r ./Bot_The_Soul_Coach ubuntu@<public-ip>:~/
```

## 4. Python env + .env

```bash
cd ~/Bot_The_Soul_Coach
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

cp .env.example .env
nano .env   # fill TELEGRAM_TOKEN, SUPERVISOR_CHAT_ID, GEMINI_API_KEY
```

Test by hand once:

```bash
python main.py
# Open Telegram, message your bot. Press Ctrl-C to stop.
```

## 5. Install the systemd unit

```bash
sudo cp deploy/soul-coach.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now soul-coach
sudo systemctl status soul-coach
journalctl -u soul-coach -f
```

## 6. Install the keepalive timer

```bash
chmod +x deploy/keepalive.sh
sudo cp deploy/keepalive.service /etc/systemd/system/
sudo cp deploy/keepalive.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now keepalive.timer
systemctl list-timers | grep keepalive
```

Verify utilization a few hours later:

```bash
# Average CPU over the last few minutes (proxy for 95p)
sar -u 1 5

# OR live:
top
```

You should see the keepalive process spike a CPU core to ~100% for 60s every 5 min. Average sits well above 20%.

## 7. Backups (local + off-host via rclone)

### 7a. Local snapshot (always, no extra setup)

```bash
mkdir -p ~/backups
crontab -e
```

```cron
# Daily snapshot at 04:15 server time
15 4 * * * /home/ubuntu/Bot_The_Soul_Coach/deploy/backup_offhost.sh
```

The script keeps 7 local copies in `~/backups/` and (once rclone is configured)
uploads to the remote with 14-day retention.

### 7b. Off-host backup via rclone (free)

1. Install rclone:

```bash
sudo apt-get install rclone
```

2. Configure a free remote (Backblaze B2 recommended — 10 GB free forever):

```bash
rclone config
# → n (new remote)
# → name: backup
# → type: b2   (or "drive" for Google Drive, "mega" for MEGA, etc.)
# Follow the prompts for API keys / OAuth
```

3. Test:

```bash
/home/ubuntu/Bot_The_Soul_Coach/deploy/backup_offhost.sh
# Check ~/backups/ for local snapshot and your rclone remote for the upload
```

4. The crontab entry from §7a calls this script, so off-host upload happens
   automatically at 04:15 daily once rclone is configured.

## 8. Uptime monitoring via /health endpoint

The bot exposes `GET /health` on port `HEALTH_PORT` (default **8080**) from a
daemon thread. Use UptimeRobot for free HTTP monitoring:

### 8a. Open port 8080 in Oracle firewall

In the Oracle Console → Networking → Virtual Cloud Networks → your VCN →
Security Lists → Default Security List → Ingress Rules → **Add Ingress Rule**:

| Field | Value |
|---|---|
| Source CIDR | `0.0.0.0/0` |
| IP Protocol | TCP |
| Destination Port Range | `8080` |

Also allow it at the OS level:

```bash
sudo iptables -I INPUT 6 -m state --state NEW -p tcp --dport 8080 -j ACCEPT
sudo netfilter-persistent save
```

### 8b. Verify

```bash
curl http://localhost:8080/health   # → ok
curl http://<public-ip>:8080/health # from your laptop → ok
```

### 8c. UptimeRobot

- Sign up at https://uptimerobot.com (free — 50 monitors).
- Add monitor: **HTTP(s)** type, URL `http://<public-ip>:8080/health`,
  check interval 5 min, keyword match `ok`.
- Set alert contact to your email or Telegram.

Optionally also add a **Heartbeat** monitor and pulse it from `keepalive.sh`
(edit the last line): `curl -fsS "$HEARTBEAT_URL" >/dev/null || true`

## 9. Operating the bot (reference)

| Task | How |
|---|---|
| Tail logs | `journalctl -u soul-coach -f` |
| Restart | `sudo systemctl restart soul-coach` |
| Update code | `cd ~/Bot_The_Soul_Coach && git pull && sudo systemctl restart soul-coach` |
| DB backup on demand | `sqlite3 data/soul_coach.db ".backup data/soul_coach.bak.db"` |
| Inspect DB | `sqlite3 data/soul_coach.db` then `.tables`, `.schema`, etc. |
| Add KB entry from Telegram | `/kb_add focus | I can't focus | <answer> | focus, distracted` (as supervisor) |

## 10. Monthly maintenance

- Log into the Oracle console at least once every 30 days (account inactivity ≥30d can lead to suspension).
- Glance at `journalctl -u soul-coach --since "7 days ago" | grep -i error`.
- Confirm `systemctl is-active keepalive.timer` returns `active`.
- Check disk: `df -h`. Backups + logs add up.

## 11. Disaster recovery

If the VM is reclaimed despite the keepalive (network outage, etc.):

1. Provision a new A1 instance per §1.
2. Restore code: `git clone …` (§3).
3. Restore DB: `scp` your latest backup to `data/soul_coach.db`.
4. Restore `.env` from your password manager.
5. Reinstall systemd units (§5–§6).

Time to recover: ~15 min if you've kept the backup file at hand.
