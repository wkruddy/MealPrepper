---
name: mealprepper-communications
description: Handle MealPrepper approvals, daily reminders, substitutions, and feedback loop. Use when editing communications agent, comms backends, or notification delivery.
---

# MealPrepper Communications

## Responsibilities

- Request approval before finalizing weekly plans
- Send daily morning meal summaries
- Parse feedback (loved/liked/disliked) and approvals
- Update preference index from feedback

## Key files

- Agent: `src/mealprepper/agents/communications.py`
- Skill: `src/mealprepper/skills/comms/communicator.py`
- Backends: `src/mealprepper/skills/comms/{slack,discord,telegram,imessage,console}.py`
- Config: `.env` (`COMMS_BACKEND`, webhook URLs, bot tokens)

## Comms backends

| `COMMS_BACKEND` | Behavior |
|-----------------|----------|
| `console` | Prints messages (default dev) |
| `slack` | POST to `SLACK_WEBHOOK_URL` |
| `discord` | POST to `DISCORD_WEBHOOK_URL` |
| `telegram` | Bot API with `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` |
| `imessage` | POST to `APPLE_SHORTCUTS_WEBHOOK_URL` (macOS) |

## Flow

1. `request_plan_approval()` → notification + DB record
2. User approves via chat or `process-feedback -m APPROVE`
3. `send_daily_summary()` → today's meals only

```bash
python -m mealprepper send-daily
python -m mealprepper process-feedback -m APPROVE
```
