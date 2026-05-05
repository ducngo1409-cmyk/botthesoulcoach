# Soul Coach Bot — Trial Guide

How to walk through every feature with a live bot. No code changes needed —
just Telegram and a browser.

> **Who this is for:** You (the developer/operator) playing both roles:
> regular user on your personal Telegram, and supervisor on the same or a second account.

---

## 0. Pre-flight (30 seconds)

SSH into your server and confirm the bot is alive:

```bash
journalctl -u soul-coach -f --no-pager | head -5
# Should show: "Soul Coach is up and running"

curl http://localhost:8080/health
# → ok
```

Find your bot's username in Telegram:
- Open **@BotFather** → `/mybots` → pick your bot → it shows `@YourBotName`

---

## PART 1 — Regular User Flow

Open Telegram on your phone or desktop. Find `@YourBotName` and start a private chat.

---

### 1.1 Onboarding + Timezone

**You type:**
```
/start
```

**Bot replies (two messages):**
```
👋 Hi [Name], I'm your Soul Coach.

I'll check in on you for the things you want to stay on top of, and
answer questions whenever you need a hand.

Try:
• /addtask Morning meditation | 0 8 * * *
• /tasks — see your reminders
• /help — full command list
• Or just message me anything that's on your mind.
```
```
🕐 What timezone are you in?

Common choices: Asia/Ho_Chi_Minh · Asia/Singapore ...

Reply with any valid tz name. Just ignore this to keep the default.
```

**You type (your actual timezone):**
```
Asia/Ho_Chi_Minh
```

**Bot replies:**
```
✅ Timezone set to Asia/Ho_Chi_Minh. Your reminders will fire in local time.
```

> Try an invalid one to see the error: type `banana` → bot warns and clears the prompt.

---

### 1.2 Add a task (real schedule)

**You type:**
```
/addtask Morning meditation | 0 8 * * *
```

**Bot replies:**
```
✅ Reminder #1 added: Morning meditation — 0 8 * * *
```

This will ping you every day at 08:00 in your timezone.

**Add a second task for testing purposes (fires every minute):**
```
/addtask Quick test | * * * * *
```

**Check your task list:**
```
/tasks
```

**Bot replies:**
```
📌 Your reminders
✅ #1  Morning meditation  —  0 8 * * *
✅ #2  Quick test  —  * * * * *
```

---

### 1.3 Receive a check-in + log mood

Wait up to 90 seconds. The `* * * * *` task fires every minute.

**Bot sends (automatically):**
```
🌱 Check-in: Quick test

How did it go? Reply with a quick note, then tap how you're feeling 👇

[😣] [😕] [😐] [🙂] [😄]
```

**You tap:** `🙂`

**Bot edits the message:**
```
🌱 Check-in: Quick test

How did it go? ...

Mood logged: 🙂
```

---

### 1.4 Pause reminders

**You type:**
```
/pause
```

**Bot replies:**
```
🔕 Reminders paused. /resume to turn them back on.
```

Wait another minute — **no check-in arrives**. The scheduler job is actually suspended, not just skipped.

**Resume:**
```
/resume
```

**Bot replies:**
```
🔔 Reminders resumed.
```

Next minute → check-in arrives again. ✓

---

### 1.5 Knowledge Base Q&A — KB direct hit

These questions match the seeded KB. Type them word-for-word to guarantee a hit.

**You type:**
```
I can't focus today
```

**Bot replies with KB answer + buttons:**
```
Try the 25/5 Pomodoro: 25 min single-task, 5 min stand-up break...

[👍 Helped]  [👎 Not really]
```

**Tap 👍:**
```
🌟 Glad that helped.
```

---

**More KB trigger phrases (try any of these):**

| What you type | KB category |
|---|---|
| `I'm feeling overwhelmed` | stress |
| `I can't sleep at night` | sleep |
| `I have no motivation to start` | motivation |
| `I keep worrying about something` | anxiety |
| `How do I build a new habit?` | habits |
| `I had a fight with someone close` | relationships |
| `I just want to talk` | general |

---

### 1.6 Q&A — LLM soft reply (KB miss)

Ask something outside the KB seed topics:

**You type:**
```
How do I deal with imposter syndrome at work?
```

**Bot replies with Gemini-generated answer:**
```
💡 _(suggestion based on coach KB)_

[Gemini answer grounded in the nearest KB entries...]

[👍 Helped]  [👎 Not really]
```

**Tap 👍** → reply saved as `kb_candidate=1` (supervisor can promote it to KB later).

**Tap 👎** → bot immediately escalates to supervisor (see Part 2 for what S receives).

---

### 1.7 Crisis filter

> This tests the safety pre-filter. The bot gives a support response — **no escalation is sent to supervisor**.

**You type:**
```
I've been feeling like I don't want to live anymore
```

**Bot replies:**
```
💙 I can hear that you're going through something really painful right now.

Please reach out to a trained professional or someone you trust —
you don't have to face this alone.

🆘 Crisis support:
• Vietnam (free, 24/7): 1800 599 920
• International directory: findahelpline.com

I'm here to listen, but I'm not a substitute for real human support.
You matter. 💙
```

**Confirm:** Check the supervisor account — **no escalation message should appear**.

---

### 1.8 Manual escalation to supervisor

**You type:**
```
/talk_to_human
```

**Bot replies to you:**
```
🙋 I've flagged this for a human coach. They'll reach out shortly.
```

**Supervisor receives (see Part 2):** an escalation card with your last 5 turns.

While you are escalated, sending any free-text message → bot stays **silent** (S is handling you directly). Reminders still fire normally.

---

### 1.9 Clean up test task

**You type:**
```
/removetask 2
```

**Bot replies:**
```
🗑 Removed reminder #2.
```

---

## PART 2 — Supervisor Flow

The supervisor is whoever has `SUPERVISOR_CHAT_ID` in `.env`. That numeric ID is your Telegram user ID — you can find it by messaging **@userinfobot**.

Open a DM with the bot **from the supervisor account** (or the same account if you're testing both roles).

---

### 2.1 List active users

**You type:**
```
/users
```

**Bot replies:**
```
👥 Active users (1):
• [Name]  tg_id=123456789  joined=2024-01-01  tasks=2
```

---

### 2.2 Knowledge Base management

**Add an entry:**
```
/kb_add mindfulness | How do I start meditating? | Start with 5 minutes. Sit, close your eyes, focus only on your breath. When your mind wanders, gently return. No app needed. | meditation, mindfulness, calm, breathe
```

**Bot replies:**
```
✅ KB entry #9 added (cat=mindfulness).
```

**List entries:**
```
/kb_list
```
or filter by category:
```
/kb_list mindfulness
```

**Edit an entry:**
```
/kb_edit 9 answer=Start with just 2 minutes. Sit, close your eyes, focus on your breath.
```

**Delete an entry:**
```
/kb_del 9
```

---

### 2.3 Receive and resolve an escalation

When a user taps 👎 on an LLM reply, or types `/talk_to_human`, you (as supervisor) receive:

```
🚨 Escalation — @username (uid 123456789)
Reason: kb_miss

Last 5 turns:
  U (10:01): How do I deal with imposter syndrome at work?
  B (10:01): 💡 (suggestion based on coach KB) [Gemini answer...]
  U (10:02): 👎 Not really

[Take over]   [Mark resolved]
```

**You can:**
- **Reply directly** in the group/DM with the user (bot is now silent for them)
- **Tap "Mark resolved"** when done → bot sends the user:
  ```
  ✅ A coach has reviewed your case. I'm back online and ready to help.
  ```
  And the user's counter resets to 0.

**Or resolve by command:**
```
/resolve 123456789
```

---

### 2.4 Promote an LLM reply to the KB

When a user gives 👍 to an LLM reply, it's flagged as a KB candidate.
The weekly report lists them. Promote it:

```
/kb_promote 47
```
(where `47` is the `interaction_id` shown in the report)

**Bot replies:**
```
✅ Promoted interaction #47 to KB as entry #10 (cat=general).
```

That answer now lives in the KB and will be returned directly next time — no LLM call.

---

### 2.5 On-demand weekly report

```
/report
```

**Bot sends two messages:**

1. Markdown summary:
```
📊 Weekly Report — 2024-W03
...
👤 Users: 2 active, 0 blocked
📋 Check-ins: 14 sent, 11 answered (79%), 3 missed
💬 Interactions: 34 total, 8 LLM-generated
🚨 Escalations: 2 (1 resolved)
🌟 KB candidates (pending /kb_promote): 3

Per-user breakdown:
• [Name]  check-ins 6/8 (75%)  mood avg 3.8  escalations 1
...
```

2. A `report_YYYY-WW.json` file (machine-readable archive).

Snippets in the report are redacted (`first 60 chars + hash`).

---

### 2.6 View full conversation transcript

```
/transcript 123456789
```
(current week — all interactions for that user)

Or a specific week:
```
/transcript 123456789 2024-03
```

**Bot replies with full verbatim history** and logs this access in `audit_log`.

---

## PART 3 — Adding the Bot to a Group

The bot is designed for **1-on-1 DMs** but it can be added to a group for shared Q&A.

### How it works in a group

- Each user who sends a message is registered individually (their own `user_id`).
- **Check-ins are always sent as private DMs** — the group sees nothing from reminders.
- Q&A, crisis filter, escalation all work the same way.
- The supervisor escalation card is always sent as a **private DM to the supervisor**, not in the group.

### Setup

1. Add the bot to the group (invite by username `@YourBotName`).
2. In group settings, make the bot an **admin** — or turn off **Bot Privacy Mode**:
   - In @BotFather → `/mybots` → your bot → **Bot Settings → Group Privacy → Turn off**
   - With privacy off, the bot sees all messages; with it on, only messages starting with `/` or mentioning the bot.
3. Members type `/start@YourBotName` to register.

### In-group command examples

```
/start@YourBotName
/addtask@YourBotName Morning standup | 0 9 * * 1-5
/help@YourBotName
```

Free-text messages (Q&A) work without the `@botname` suffix if privacy mode is off.

### Group limitations

| Feature | Works in group? |
|---|---|
| Q&A (KB + LLM) | ✅ Yes |
| Crisis filter | ✅ Yes |
| Satisfaction buttons | ✅ Yes |
| /start, /help, /tasks | ✅ Yes (with @botname) |
| Check-in reminders | ⚠️ Fires as private DM, not in group |
| Escalation | ⚠️ Escalation card goes to supervisor DM, not group |
| Supervisor commands | ✅ But only if sent by the supervisor user_id |

**Recommended:** Keep supervisor commands in a private DM with the bot. Use the group only for user-facing Q&A.

---

## PART 4 — Quick Feature Test Shortcuts

Use these when you just want to verify one specific feature is working.

| What to test | Exact message | Expected |
|---|---|---|
| KB hit | `I can't focus today` | KB answer + 👍👎 |
| KB miss → LLM | `what is cognitive reframing?` | 💡 prefix + 👍👎 |
| Crisis filter | `I want to kill myself` | 💙 hotline reply, no escalation |
| Manual escalation | `/talk_to_human` | Escalation card to supervisor |
| Reminder (fast) | `/addtask Test \| * * * * *` then wait 60s | Check-in DM arrives |
| Pause | `/pause` then wait 60s | No check-in arrives |
| Resume | `/resume` then wait 60s | Check-in arrives again |
| Satisfaction counter | Send 5 negative messages in a row: `still stuck`, `nope`, `doesn't help`, `same problem`, `vẫn vậy` | Escalates on 5th |
| Timezone set | `/start` (if new user) → reply `Europe/Paris` | Timezone updated |
| Supervisor KB | `/kb_add test \| Q? \| A. \| kw` | Entry #N added |
| Weekly report | `/report` (as supervisor) | Markdown + JSON |
| Transcript | `/transcript YOUR_USER_ID` (as supervisor) | Full chat history |
| Health check | `curl http://localhost:8080/health` in SSH | `ok` |

---

## PART 5 — Check What's in the DB

Useful to verify data is being saved correctly.

```bash
# SSH into server
sqlite3 ~/Bot_The_Soul_Coach/data/soul_coach.db

-- Who's registered?
SELECT tg_id, name, tz, status FROM users;

-- What tasks are scheduled?
SELECT id, user_id, title, cron_expr, active FROM tasks;

-- Recent check-ins
SELECT id, user_id, status, mood, sent_at FROM check_ins ORDER BY id DESC LIMIT 10;

-- Recent Q&A interactions
SELECT id, direction, text, llm, satisfied FROM interactions ORDER BY id DESC LIMIT 10;

-- KB entries
SELECT id, category, question, hits FROM kb_entries;

-- Escalations
SELECT id, user_id, reason, sent_to_s_at, resolved_at FROM escalations;

-- Satisfaction session state
SELECT user_id, sat_counter, escalated_at FROM sessions;

.quit
```

---

## Common Issues

| Symptom | Likely cause | Fix |
|---|---|---|
| Bot doesn't respond at all | TELEGRAM_TOKEN wrong or bot not running | `journalctl -u soul-coach -f` — check for errors |
| KB answer not matching | Phrase too different from seed question | Try exact phrases from §1.5 table |
| LLM reply is a fallback error message | GEMINI_API_KEY wrong or quota hit | Check `logs/bot.err.log` |
| Check-in never arrives | Task cron or timezone wrong | Check `/tasks`; use `* * * * *` to test |
| Supervisor commands not working | Sending from wrong account | Your `user_id` must match `SUPERVISOR_CHAT_ID` in `.env` |
| Escalation not arriving to supervisor | Wrong `SUPERVISOR_CHAT_ID` | DM @userinfobot to get your real numeric id |
| Group bot not seeing messages | Privacy mode on | @BotFather → Bot Settings → Group Privacy → Turn off |
