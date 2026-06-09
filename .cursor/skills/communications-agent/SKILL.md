---
name: communications-agent
description: Guide for the Communications Agent — Slack/Discord/Telegram/iMessage approval, daily summaries, feedback. Use when editing communications agent, comms backends, watch-messages, or process-feedback CLI.
---

# Communications Agent

## Purpose

Send approval requests and daily meal summaries; parse feedback and approvals; update preferences.

## Key files

- `src/mealprepper/agents/communications.py`
- `src/mealprepper/skills/comms/` — outbound backends
- `src/mealprepper/skills/comms/slack_bot.py` — inbound Slack Socket Mode listener
- `src/mealprepper/skills/comms/bot_commands.py` — command dispatch
- `docs/SLACK_BOT.md` — full Slack app setup

## Outbound (`COMMS_BACKEND`)

`console` | `slack` | `discord` | `telegram` | `imessage`

## Inbound Slack bot

```bash
pip install -e ".[slack]"
mealprepper watch-messages
```

Requires `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, optional `SLACK_CHANNEL_ID`.

Bot commands: `approve`, `reject`, `status`, `plan`, `daily`, `grocery`, `help`, `loved <meal>`.

## CLI fallback

```bash
mealprepper process-feedback -m "APPROVE"
mealprepper process-feedback -m "loved sheet pan chicken"
```
