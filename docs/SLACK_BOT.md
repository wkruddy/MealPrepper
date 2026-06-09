# Slack bot setup (MealPrepper)

MealPrepper uses **Slack Socket Mode** so your Linux VM can receive messages without a public URL or OAuth redirect. Outbound notifications use an **Incoming Webhook**; inbound commands use a **Bot** over Socket Mode.

## Architecture

| Direction | Mechanism | Env var |
|-----------|-----------|---------|
| Outbound (plan approval, daily summary) | Incoming Webhook | `SLACK_WEBHOOK_URL` |
| Inbound (approve, status, feedback) | Bot + Socket Mode | `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN` |

Run the listener on your VM:

```bash
pip install -e ".[slack]"
mealprepper watch-messages
```

## 1. Create the Slack app

1. Go to [api.slack.com/apps](https://api.slack.com/apps) → **Create New App** → **From scratch**.
2. Name it `MealPrepper` (or similar) and pick your workspace.

## 2. Enable Socket Mode

1. **Settings → Socket Mode** → toggle **ON**.
2. **Basic Information → App-Level Tokens** → **Generate Token and Scopes**.
3. Scope: `connections:write`
4. Copy the token (`xapp-...`) → `SLACK_APP_TOKEN` in `.env`.

## 3. Bot token scopes

**OAuth & Permissions → Scopes → Bot Token Scopes** — add:

| Scope | Why |
|-------|-----|
| `chat:write` | Bot replies in channels |
| `channels:history` | Read messages in public channels |
| `groups:history` | Read messages in private channels (if you use a private `#food`) |
| `app_mentions:read` | Respond when @mentioned |
| `commands` | Slash commands |

**User Token Scopes:** none required.

Click **Install to Workspace** (or **Reinstall** after adding scopes). Copy **Bot User OAuth Token** (`xoxb-...`) → `SLACK_BOT_TOKEN`.

## 4. Event subscriptions

**Event Subscriptions** → toggle **ON**.

Under **Subscribe to bot events**, add:

- `message.channels` — messages in public channels the bot is in
- `message.groups` — messages in private channels (optional)
- `app_mention` — @MealPrepper mentions

No Request URL is needed when Socket Mode is enabled.

## 5. Slash commands

**Slash Commands → Create New Command**:

| Field | Value |
|-------|-------|
| Command | `/mealprepper` |
| Request URL | `https://example.com` (placeholder — Socket Mode delivers commands) |
| Short description | Family meal planning |
| Usage hint | `approve \| status \| plan \| daily \| grocery \| help` |

Optional alias: `/mp` with the same settings.

## 6. Incoming webhook (outbound only)

1. **Incoming Webhooks** → toggle **ON** → **Add New Webhook to Workspace**.
2. Pick your family channel (e.g. `#food`).
3. Copy webhook URL → `SLACK_WEBHOOK_URL`.

## 7. Invite the bot

In Slack:

```
/invite @MealPrepper
```

in `#food` (or your chosen channel).

## 8. `.env` on the VM

```bash
COMMS_BACKEND=slack

# Outbound notifications
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T.../B.../...

# Inbound bot (watch-messages)
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...

# Recommended: restrict bot to one channel (right-click channel → View channel details → copy Channel ID)
SLACK_CHANNEL_ID=C0123456789
```

## 9. Run the bot

```bash
pip install -e ".[slack]"
mealprepper watch-messages
```

Keep it running via systemd (see below) or `tmux`.

## Bot commands

In `#food`, type commands directly (e.g. `approve`, `status`). In other channels, use `@MealPrepper` or `/mealprepper`.

| Command | What it does |
|---------|----------------|
| `help` | List commands |
| `approve` | Approve pending weekly plan |
| `reject` | Reject pending plan |
| `status` | Plan status + pending approval |
| `plan` | This week's meal titles (structured by day) |
| `plan-recipes` | Full week with ingredients and steps (one message per day) |
| `plan-week` | Start weekly replan — shows warning first |
| `confirm plan-week` | Proceed after reading the warning (takes several minutes) |
| `cancel` | Cancel a pending confirmation |
| `daily` | Today's meals from the active approved plan |
| `grocery` | Build grocery list (plan must be approved) |
| `recipes` | List family recipe library (`recipes chicken` to search) |
| `recipe <name>` | Show saved or planned recipe steps |
| `add-recipe <text>` | Save a meal idea to the family library |
| `loved chicken tacos` | Record meal feedback |
| `liked` / `disliked` / `neutral` | Feedback on recent dinner |

Slash form: `/mealprepper approve`, `/mealprepper status`, etc.

## systemd (always-on bot)

After `./scripts/install_systemd.sh`, enable the listener:

```bash
systemctl --user enable --now mealprepper-watch-messages.service
systemctl --user status mealprepper-watch-messages.service
```

Logs (systemd service):
- `data/logs/watch-messages.log` — startup banner (stdout)
- `data/logs/watch-messages.err` — command traffic, LLM calls, errors (stderr)

```bash
tail -F data/logs/watch-messages.log data/logs/watch-messages.err
# or: logsMPBot  (if alias is in ~/.bashrc)
```

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `not_in_channel` | `/invite @MealPrepper` in the channel |
| Bot never replies | Check `SLACK_CHANNEL_ID` matches; verify Event Subscriptions |
| `missing_scope` | Reinstall app after adding bot scopes |
| Outbound works, inbound doesn't | Webhook ≠ bot — need `watch-messages` + bot tokens |
| Slash command "did not respond" | Slow commands (`grocery`, `plan-week`) ack immediately and post results when done — restart the bot after upgrades |
| `recipe` returns wrong meal | Fixed: weekly plan meals are matched before the saved recipe library |
| `invalid_auth` on Socket Mode | Regenerate `SLACK_APP_TOKEN` with `connections:write` |

## Security notes

- Treat `SLACK_BOT_TOKEN` and `SLACK_APP_TOKEN` like passwords.
- Use a dedicated `#food` channel and set `SLACK_CHANNEL_ID`.
- Only workspace members in that channel can trigger commands.
