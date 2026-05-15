# CHIEF — User Guide

CHIEF is your AI operations assistant for Cascadia OS. Send it a task in plain
English or use slash commands for 100% routing accuracy.

---

## Quick Start

Open Telegram and message **@ZyrconBot**.

---

## Commands (100% accurate)

Commands bypass the AI entirely — they route instantly to the right operator.

| Command            | What it does                         |
|--------------------|--------------------------------------|
| `/recon`           | Run a RECON lead scan                |
| `/scan`            | Alias for `/recon`                   |
| `/leads`           | Show lead report                     |
| `/quote [job]`     | Draft a proposal or quote            |
| `/scout`           | Qualify an inbound lead              |
| `/status`          | System health check                  |
| `/operators`       | List available operators             |
| `/help`            | Show all commands                    |

---

## Plain English Examples

These all work without commands — CHIEF's AI figures out what you need:

- `"Find me HVAC contractors in Houston"`
- `"Draft a proposal for a warehouse mezzanine job"`
- `"How many leads do we have?"`
- `"Run another scan"`
- `"Do it again"`
- `"Qualify this inbound lead"`

---

## What CHIEF Can Do Today

| Operator         | What it does                                       | Command     |
|------------------|----------------------------------------------------|-------------|
| 🔍 RECON         | Find contractor leads by trade and location        | `/recon`    |
| 📄 Quote Brief   | Draft job proposals and quotes                     | `/quote`    |
| 🎯 SCOUT         | Score and qualify inbound prospects                | `/scout`    |

---

## Coming Soon

| Feature            | Status     |
|--------------------|------------|
| 📧 Email Outreach  | Roadmap    |
| 📱 Social Posting  | Roadmap    |
| 🧾 Invoicing       | Roadmap    |
| 📅 Scheduling      | Roadmap    |

---

## Multi-Turn Conversation

CHIEF remembers the last 3 exchanges in your conversation. After a RECON scan
you can follow up naturally:

```
You:   "Run recon"
CHIEF: "🔍 RECON scan started. I'll message you when it's done."
...
CHIEF: "✅ Scan complete. +35 new leads added. ..."

You:   "Do it again"
CHIEF: "🔍 RECON scan started..."   ← knows what "it" means

You:   "How many of those have emails?"
CHIEF: → references the RECON result above
```

---

## Tips

- Use `/recon` for a quick scan; plain English for a more specific request.
- After a scan, ask `"how many contacts?"` for a summary.
- Say `"do it again"` to repeat the last action.
- Use `/help` anytime to see available commands.
- Use `/status` to check if all systems are online.
