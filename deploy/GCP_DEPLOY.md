# Deploy on Google Cloud Platform — Always Free

End-to-end playbook for the **e2-micro** Always Free VM
(0.25 vCPU burst / 1 GB RAM, Ubuntu 22.04 LTS).

> **Free tier guarantee.**
> The e2-micro is permanently free — it does not expire after a trial period.
> GCP does **not** reclaim VMs for low CPU usage, so no keepalive script is needed.
> You must keep billing enabled on the account (required to use even free resources),
> but you will not be charged as long as you stay within the free-tier limits.
> Set a $1 budget alert as a tripwire (see §1 step 5).

---

## Always Free limits that matter

| Resource | Free allowance |
|---|---|
| e2-micro VM | 1 instance/month (us-west1, us-central1, or us-east1 only) |
| Standard persistent disk | 30 GB |
| Snapshot storage | 5 GB |
| Network egress | 1 GB/month to most destinations |
| Cloud Storage (for rclone backups) | 5 GB (Standard, us regions) |

The bot + SQLite DB + logs easily fit within these limits.

---

## 1. Create the VM

1. Go to https://console.cloud.google.com and sign in.
2. If this is a new account, GCP gives a **90-day / $300 free trial** first. After
   it expires the account automatically drops to Always Free — you will not be
   charged unless you manually upgrade.
3. Enable billing on your project (required even for free tier).
4. Navigate to **Compute Engine → VM instances → Create instance**.
5. Fill in:
   - **Name**: `soul-coach`
   - **Region**: `us-central1` (Iowa) — or `us-west1` / `us-east1`
   - **Zone**: any zone in that region
   - **Machine configuration**: Series `E2`, Machine type **e2-micro**
   - **Boot disk**: Ubuntu 22.04 LTS, Standard persistent disk, **30 GB**
   - **Firewall**: tick "Allow HTTP traffic" (we'll lock it down later) — or leave
     unticked and add a specific rule in §2.
6. **Create**. Wait ~1 min.
7. Set a budget alert: **Billing → Budgets & alerts → Create budget**,
   amount `$1`, notify at 50 % and 100 %. Emails you before you're ever charged.

---

## 2. Open firewall for the health endpoint

The bot's `/health` HTTP check listens on port **8080**. Open it in the GCP
firewall (separate from the VM's OS firewall):

```bash
gcloud compute firewall-rules create soul-coach-health \
  --allow tcp:8080 \
  --source-ranges 0.0.0.0/0 \
  --target-tags soul-coach-health \
  --description "Allow UptimeRobot to reach /health"
```

Then add the tag to the VM:

```bash
gcloud compute instances add-tags soul-coach \
  --tags soul-coach-health \
  --zone us-central1-a   # adjust to your zone
```

Or do it in the Console: **VM instance → Edit → Network tags** → add
`soul-coach-health`, then **VPC network → Firewall → Create rule**.

---

## 3. SSH + base setup

```bash
# From your laptop (gcloud CLI) — or use the in-browser SSH button
gcloud compute ssh soul-coach --zone us-central1-a

# On the VM:
sudo apt-get update && sudo apt-get upgrade -y
sudo apt-get install -y python3 python3-venv python3-pip git sqlite3 rclone
```

> **Python version note:** Ubuntu 22.04 ships Python 3.10 which is fine.
> If you want 3.11+: `sudo apt-get install python3.11 python3.11-venv`

---

## 4. Get the code onto the VM

```bash
cd ~
git clone https://github.com/ducngo1409-cmyk/botthesoulcoach.git Bot_The_Soul_Coach
cd Bot_The_Soul_Coach
```

---

## 5. Python env + .env

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

cp .env.example .env
nano .env   # fill TELEGRAM_TOKEN, SUPERVISOR_CHAT_ID, GEMINI_API_KEY
```

Make the data and logs directories:

```bash
mkdir -p data logs
```

Test by hand once:

```bash
python main.py
# Open Telegram, message your bot. Ctrl-C to stop.
# Also verify: curl http://localhost:8080/health  →  ok
```

---

## 6. Install the systemd unit

```bash
# The GCP unit uses the current $USER (your SSH username, e.g. "alice_example")
# Substitute YOUR_USER below, or run the sed command to do it automatically:

MYUSER=$(whoami)
sed "s/__USER__/$MYUSER/g" \
    ~/Bot_The_Soul_Coach/deploy/soul-coach-gcp.service \
    | sudo tee /etc/systemd/system/soul-coach.service

sudo systemctl daemon-reload
sudo systemctl enable --now soul-coach
sudo systemctl status soul-coach
journalctl -u soul-coach -f
```

---

## 7. No keepalive needed

GCP does **not** reclaim e2-micro instances for low CPU usage. The Oracle
`keepalive.timer` and `keepalive.service` are **not needed** — do not install
them.

---

## 8. Backups (local + Cloud Storage via rclone)

### 8a. Configure rclone → Google Cloud Storage (free 5 GB)

```bash
rclone config
# → n  (new remote)
# → name: backup
# → type: google cloud storage
# → project_number: (your GCP project number, from console.cloud.google.com)
# → auth: use service account or browser OAuth
# → bucket_policy: private
# Confirm with defaults for the rest.
```

Create a bucket (free in the same region as your VM):

```bash
gsutil mb -l us-central1 gs://soul-coach-backup-$(gcloud config get-value project)
```

Update `RCLONE_REMOTE` in your crontab:

```bash
export RCLONE_REMOTE="backup:soul-coach-backup-$(gcloud config get-value project)"
```

### 8b. Schedule the backup cron

```bash
mkdir -p ~/backups
crontab -e
```

```cron
# Daily backup at 04:15 UTC
20 4 * * * RCLONE_REMOTE="backup:soul-coach-backup-YOUR_PROJECT_ID" \
  /home/YOUR_USER/Bot_The_Soul_Coach/deploy/backup_offhost.sh
```

Test immediately:

```bash
RCLONE_REMOTE="backup:soul-coach-backup-YOUR_PROJECT_ID" \
  ~/Bot_The_Soul_Coach/deploy/backup_offhost.sh
```

---

## 9. Uptime monitoring via /health

```bash
# Verify from the VM:
curl http://localhost:8080/health    # → ok

# Verify from your laptop (public IP from console):
curl http://<external-ip>:8080/health  # → ok
```

**UptimeRobot setup:**
- Sign up at https://uptimerobot.com (free — 50 monitors).
- **Add new monitor**: type **HTTP(s)**, URL `http://<external-ip>:8080/health`,
  interval 5 min, keyword `ok`.
- Set alert contact (email or Telegram).

---

## 10. Operating the bot

| Task | Command |
|---|---|
| Tail logs | `journalctl -u soul-coach -f` |
| Restart | `sudo systemctl restart soul-coach` |
| Update code | `cd ~/Bot_The_Soul_Coach && git pull && sudo systemctl restart soul-coach` |
| DB backup on demand | `sqlite3 data/soul_coach.db ".backup data/soul_coach.bak.db"` |
| Check disk usage | `df -h && du -sh ~/Bot_The_Soul_Coach/data ~/Bot_The_Soul_Coach/logs ~/backups` |
| View external IP | `curl -s ifconfig.me` |

---

## 11. Monthly maintenance

- GCP does not have account inactivity suspension — no need to log in monthly.
- Check logs: `journalctl -u soul-coach --since "7 days ago" | grep -i error`
- Confirm backup cron ran: `tail ~/Bot_The_Soul_Coach/logs/backup.log`
- Disk check: `df -h` (30 GB limit; logs + backups grow slowly)

---

## 12. Disaster recovery

If the VM is ever deleted or corrupted:

1. Create a new e2-micro in the same region (§1).
2. `git clone https://github.com/ducngo1409-cmyk/botthesoulcoach.git Bot_The_Soul_Coach`
3. Restore DB from rclone remote:
   ```bash
   rclone copy backup:soul-coach-backup-YOUR_PROJECT/soul_coach.YYYYMMDD.db \
     ~/Bot_The_Soul_Coach/data/soul_coach.db
   ```
4. Restore `.env` from your password manager.
5. Reinstall systemd unit (§6).

Time to recover: ~10 min.

---

## GCP vs Oracle — key differences

| | GCP e2-micro | Oracle A1 Flex |
|---|---|---|
| vCPU | 0.25 (burst to 2) | 2 OCPU |
| RAM | 1 GB | 12 GB |
| Disk | 30 GB standard | 50 GB |
| Idle reclamation | ❌ Never | ✅ Yes (if CPU < 20%) |
| Keepalive needed | ❌ No | ✅ Yes |
| Region lock (free) | us-west1/central1/east1 | Any always-free region |
| Account inactivity | No suspension | 30d inactivity can suspend |
| Billing required | Yes (but $0 charged) | No |

The bot runs comfortably on GCP's 1 GB RAM. Peak RSS observed: ~180–300 MB
(Python + libraries + open DB connection). No swap needed.
