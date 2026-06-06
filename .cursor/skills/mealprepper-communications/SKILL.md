---
name: mealprepper-communications
description: Handle MealPrepper approvals, daily SMS reminders, substitutions, and feedback loop. Use when editing communications agent, sms_communicator, or SMS backends.
---

# MealPrepper Communications Agent

## Role

- Request approval before finalizing weekly plans
- Send morning daily meal summaries
- Handle substitutions from user feedback
- Collect liked/disliked meals for preference learning

## Key files

- Agent: `src/mealprepper/agents/communications.py`
- Skill: `src/mealprepper/skills/sms_communicator.py`
- Models: `src/mealprepper/models/feedback.py`
- Config: `.env` (`SMS_BACKEND`, Twilio/Apple Shortcuts vars)

## SMS backends

| `SMS_BACKEND` | Behavior |
|---------------|----------|
| `console` | Prints to terminal (default dev) |
| `twilio` | Requires `pip install mealprepper[twilio]` + Twilio env vars |
| `apple_shortcuts` | POST JSON `{to, message}` to `APPLE_SHORTCUTS_WEBHOOK_URL` |

## Approval flow

1. `request_plan_approval()` → SMS summary + DB record
2. User replies APPROVE or suggests changes
3. `approve-plan <id>` or `--reject` via CLI

## Daily reminders

Sent at `DAILY_REMINDER_HOUR` (default 7). Include infant BLW tips and dinner prep timing (start ~4:45pm for 5:30 toddler dinner).

## Feedback loop

```bash
python -m mealprepper add-feedback "Meal Name" --rating loved
python -m mealprepper process-feedback
```

Ratings: `loved`, `liked`, `neutral`, `disliked`, `reject`

Updates `PreferenceProfile` in SQLite for next week's meal finder prompts.

## Cron / systemd

Linux server:

```bash
./scripts/install_systemd.sh
# or manually:
./scripts/cron/run_scheduled.sh daily
./scripts/cron/run_scheduled.sh feedback
```
