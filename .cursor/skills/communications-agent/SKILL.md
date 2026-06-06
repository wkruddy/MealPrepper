---
name: communications-agent
description: Guide for the Communications Agent — SMS approval, daily summaries, feedback. Use when editing communications agent, SMS backends, or send-daily/process-feedback CLI.
---

# Communications Agent

## Role

Send approval requests and daily meal summaries via SMS; parse feedback and approvals; update preferences.

## Key files

- `src/mealprepper/agents/communications.py`
- `src/mealprepper/skills/sms/` — pluggable backends (console default)
- `src/mealprepper/skills/comms_formatter.py` — SMS text formatting
- `src/mealprepper/skills/feedback_collector.py` — parse inbound messages
- `src/mealprepper/skills/preference_learner.py` — apply feedback to profile

## SMS backends

Set `SMS_BACKEND` in `.env`:

| Value | Behavior |
|-------|----------|
| `console` | Print to stdout (dev default) |
| `twilio` | Twilio REST API (recommended on Linux servers) |
| `apple_shortcuts` | Webhook to macOS Shortcuts |
| `imsg` | macOS-only stub for native iMessage |

## CLI

```bash
mealprepper send-daily [--date YYYY-MM-DD]
mealprepper process-feedback -m "APPROVE"
mealprepper process-feedback -m "loved sheet pan chicken"
mealprepper watch-messages   # stub
```

## Approval flow

After `plan-week`, agent sends SMS summary. User replies `APPROVE` → plan status becomes `approved` → `generate-grocery` can run.
