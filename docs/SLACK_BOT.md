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

### systemd (OAuth server — on-demand only)

The OAuth callback server is **only needed during workspace install**, not 24/7 alongside `watch-messages`. Socket Mode handles day-to-day bot traffic without a public URL.

**Slack requires HTTPS redirect URLs** — `http://127.0.0.1:8787` is rejected by most apps. Use **ngrok** (see [Multi-workspace install](#multi-workspace-install-developer-app--friends-workspace)) and set `SLACK_OAUTH_REDIRECT_URI` in `.env` before starting the service.

`./scripts/install_systemd.sh` writes `mealprepper-oauth-server.service` (disabled by default). Start it when a workspace admin is about to approve the install link, then stop it when done:

```bash
# Start before sending the authorize URL (ngrok must already be running)
systemctl --user start mealprepper-oauth-server.service
systemctl --user status mealprepper-oauth-server.service

# After OAuth succeeds (or you cancel), stop it
systemctl --user stop mealprepper-oauth-server.service
```

Logs:
- `data/logs/oauth-server.log` — authorize URL and callback status (stdout)
- `data/logs/oauth-server.err` — OAuth server diagnostics (stderr)

The service reads `SLACK_OAUTH_REDIRECT_URI` from `.env` via `EnvironmentFile`. That value must match **exactly** what you registered at api.slack.com → **OAuth & Permissions → Redirect URLs**.

Optional: enable only during onboarding windows (`systemctl --user enable mealprepper-oauth-server.service`), but most installs should use manual `start` / `stop` instead of leaving it running.

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `not_in_channel` | `/invite @MealPrepper` in the channel |
| Bot never replies | Check `SLACK_CHANNEL_ID` matches; verify Event Subscriptions |
| `missing_scope` | Reinstall app after adding bot scopes |
| Outbound works, inbound doesn't | Webhook ≠ bot — need `watch-messages` + bot tokens |
| Slash command "did not respond" | Slow commands (`grocery`, `plan-week`) ack immediately and post results when done — restart the bot after upgrades |
| `recipe` returns wrong meal | Fixed: weekly plan meals are matched before the saved recipe library |
| `cannot_reply_to_message` in logs | Usually duplicate @mention handling or forced threading — bot retries without thread_ts |
| Bot replies in thread **and** main channel | Fixed: slash commands no longer use `in_channel` responses (use `/mp` or plain `start` in thread) |
| `invalid_auth` on Socket Mode | Regenerate `SLACK_APP_TOKEN` with `connections:write` |

## Multi-workspace install (developer app → friend's workspace)

Use this when **you** own the Slack app at [api.slack.com](https://api.slack.com/apps) but **someone else** owns the target workspace (e.g. Hollyw00t dev workspace vs Fractal Productions).

**Important:** A Slack **workspace is not a household**. OAuth installs the app at the workspace level (bot token + webhook + default channel). Each Slack user in that workspace runs `start` in Slack to create **their own** household (e.g. "Alex's Family"). Multiple households can exist in one workspace.

Socket Mode uses one **app-level token** (`SLACK_APP_TOKEN`, `xapp-...`) for all workspaces. Each workspace needs its own **bot token** (`xoxb-...`) stored in `slack_bindings`.

### Hollyw00t (legacy single-household per workspace)

The dev `default` family with a `family_id` on `slack_bindings` still works: every user in that workspace shares the same household. `SLACK_BOT_TOKEN` in `.env` is the fallback when no per-workspace token is stored.

### Common install mistakes

| Mistake | Why it fails |
|---------|----------------|
| Redirect URL = `http://127.0.0.1:8787/...` | Slack apps **reject non-HTTPS** redirect URLs — use ngrok |
| Redirect URL = `https://fractalfaction.slack.com/...` | Workspace URLs are **not** valid OAuth redirect URIs |
| `redirect_uri=` empty in install link | No Redirect URL registered in app settings — Slack cannot complete OAuth |
| `SLACK_OAUTH_REDIRECT_URI` ≠ api.slack.com entry | Must match **exactly** (scheme, host, path) |
| Old Salesforce redirect still listed | Remove `https://auth.slack-apps.salesforce.com/slack_oauth_callback/...` unless you use Salesforce |
| Workspace bound but bot says "not configured" | Run `mealprepper slack bind-workspace` and restart `watch-messages` |
| ngrok not running when admin clicks Allow | Callback never reaches MealPrepper — start ngrok before oauth-server |

### OAuth install (ngrok + HTTPS — required)

Slack apps require **HTTPS** redirect URLs. Run ngrok on the VM, register the ngrok URL in api.slack.com, set `.env`, then start the OAuth server.

**Step-by-step:**

```bash
# 1. Tunnel port 8787 (keep this terminal open)
ngrok http 8787
```

Copy the **Forwarding** HTTPS URL (e.g. `https://abc123.ngrok-free.app`).

```bash
# 2. api.slack.com → your app → OAuth & Permissions → Redirect URLs → Add:
#    https://abc123.ngrok-free.app/slack/oauth/callback

# 3. .env (must match step 2 exactly)
SLACK_CLIENT_ID=324010600710.11314433222885
SLACK_CLIENT_SECRET=<from api.slack.com → Basic Information>
SLACK_OAUTH_REDIRECT_URI=https://abc123.ngrok-free.app/slack/oauth/callback
```

```bash
# 4. Start OAuth callback server (systemd or foreground)
systemctl --user start mealprepper-oauth-server.service
# Or foreground (no --family-slug — workspace-only install):
# mealprepper slack oauth-server --host 0.0.0.0
```

```bash
# 5. Print authorize URL (reads SLACK_OAUTH_REDIRECT_URI from .env)
mealprepper slack authorize-url
```

Send the printed URL to the **target workspace admin**. They click **Allow**; Slack redirects through ngrok to MealPrepper, which exchanges the code and saves the **workspace binding** automatically (when `--family-slug` is omitted).

Copy the authorize URL from `data/logs/oauth-server.log` if you use systemd instead of step 5.

### After install — bind workspace (if OAuth did not auto-save)

Use tokens from the OAuth success page:

```bash
mealprepper slack bind-workspace \
  --workspace-id T0FF2EHDM \
  --channel-id C0RNHDZ08 \
  --bot-token xoxb-... \
  --webhook-url https://hooks.slack.com/...

systemctl --user restart mealprepper-watch-messages
```

Verify:

```bash
mealprepper slack list-workspaces
```

In Slack: `/invite @MealPrepper` in your channel, then reply `start` to create your household.

### Fix redirect URLs (api.slack.com)

1. Open your app → **OAuth & Permissions**.
2. Under **Redirect URLs**, **remove** any invalid entries (workspace URLs, `http://127.0.0.1`, Salesforce callback unless needed).
3. **Add** your ngrok HTTPS callback:

| Redirect URL to add |
|---------------------|
| `https://YOUR-SUBDOMAIN.ngrok-free.app/slack/oauth/callback` |

Save URLs. The value must match **exactly** (including `https`, subdomain, and path).

### Option A — OAuth callback server (recommended)

On the machine running MealPrepper (after ngrok + `.env` above):

```bash
pip install -e ".[slack]"

mealprepper slack oauth-server --host 0.0.0.0
```

Omit `--family-slug` for workspace-only install (recommended for friend workspaces). Pass `--family-slug hollyw00t` only for legacy single-household binding.

Uses `SLACK_OAUTH_REDIRECT_URI` from `.env` automatically. Override with `--redirect-uri` only if needed.

Or use the systemd unit — see [systemd (OAuth server — on-demand only)](#systemd-oauth-server--on-demand-only).

The command prints an **authorize URL**. Send that link to the **Fractal workspace admin**. They click **Allow**, Slack redirects to your callback, and MealPrepper exchanges the code for a bot token.

### Option B — Manual authorize URL

Print a URL using `SLACK_OAUTH_REDIRECT_URI` from `.env`:

```bash
mealprepper slack authorize-url
```

Or pass an explicit redirect:

```bash
mealprepper slack authorize-url \
  --redirect-uri https://abc123.ngrok-free.app/slack/oauth/callback
```

Full URL shape (scopes must match your app):

```
https://slack.com/oauth/v2/authorize?client_id=324010600710.11314433222885&scope=chat:write,channels:history,groups:history,app_mentions:read,commands&redirect_uri=https%3A%2F%2Fabc123.ngrok-free.app%2Fslack%2Foauth%2Fcallback
```

Run `mealprepper slack oauth-server` (or `systemctl --user start mealprepper-oauth-server.service`) **before** the admin opens the link so the callback can exchange the `code`. ngrok must be running.

### Option C — Install from api.slack.com dashboard (same org only)

If you are admin of **both** workspaces: **Install App** → pick workspace → copy **Bot User OAuth Token** from **OAuth & Permissions**. This does not need a redirect URL but only works for workspaces you can select in the dropdown.

### After install — bind Fractal workspace

**Option A — workspace only (no household yet; users say `start` in Slack):**

```bash
mealprepper slack bind-workspace \
  --workspace-id T0FF2EHDM \
  --bot-token 'xoxb-...' \
  --channel-id C0RNHDZ08 \
  --webhook-url 'https://hooks.slack.com/services/...'
```

OAuth without `--family-slug` now saves this automatically.

**Option B — workspace + household in one step:**

```bash
mealprepper family add-slack-binding \
  --slug fractal \
  --name "Fractal Productions" \
  --workspace-id T0FF2EHDM \
  --channel-id C0RNHDZ08 \
  --bot-token 'xoxb-...' \
  --webhook-url 'https://hooks.slack.com/services/...'
```

Verify:

```bash
mealprepper slack list-workspaces
mealprepper family list
```

Restart the bot:

```bash
systemctl --user restart mealprepper-watch-messages
```

In Fractal Slack: `/invite @MealPrepper` in any channel, then `start` to create your household.

Bound workspaces respond in **any channel** the bot is invited to. `SLACK_CHANNEL_ID` in `.env` only filters the legacy dev workspace when it has no DB binding.

### Per-user household onboarding

1. User sends `start`
2. Bot asks for a household name (e.g. "Alex's Family") **in a thread** anchored on the user's `start` message
3. User replies in that thread with the name, then `confirm`
4. Bot asks a short setup questionnaire (diet, nutrition goal, food preferences, who's eating) — saved to that user's household
5. Each Slack user gets their own household — multiple households can exist in one workspace

Follow-up answers during setup are accepted even when they are not known commands. Say `skip` on any question or `skip setup` to finish later. Use `household` to see your saved preferences.

### Verify install succeeded

| Check | Hollyw00t (dev) | Fractal (friend) |
|-------|-----------------|------------------|
| api.slack.com → **Install App** | Listed | **Must also be listed** |
| Bot token | `xoxb-...` for T9J0AHNLW | Separate `xoxb-...` for Fractal `team_id` |
| `mealprepper slack list-workspaces` | May show dev workspace + household | Shows workspace; household pending until users `start` |
| Bot reply to `start` | N/A if legacy binding | Onboarding flow begins |
| Bot reply to `help` | Works in dev channel | Works after household created |

**"Chokes on next steps"** usually means one of:

- **`redirect_uri did not match`** — Redirect URL in api.slack.com ≠ URL in authorize link
- **Blank page / `invalid_code`** — Callback server was not running when Slack redirected
- **Bot says workspace not connected** — Run `mealprepper slack bind-workspace` (or re-run OAuth)
- **Bot says not configured for this channel** — Workspace not in DB; `SLACK_CHANNEL_ID` blocks unbound workspaces
- **App not in channel** — Run `/invite @MealPrepper`

### Multi-family CLI reference

```bash
# Overview of all households (shared + per-user Slack onboarding)
mealprepper family list
mealprepper family list --verbose

# One household in detail (members, Slack users, recipe/plan counts)
mealprepper family show <slug>

# Remove a household (debug / reset a bad Slack signup)
mealprepper family remove <slug> --dry-run
mealprepper family remove <slug> --yes

# Debug: which Slack user owns which household
mealprepper family list-users
mealprepper family list-users --workspace-id T0FF2EHDM

# Inspect a family's recipe library (users manage via Slack; you can debug here)
mealprepper list-recipes --family-slug <slug>
mealprepper show-plan --family-slug <slug>

In Slack, each user runs `household` to see their own saved data; `settings`, `status`, and `recipes` are scoped to that household.

# Workspace installs
mealprepper slack bind-workspace --workspace-id ... --bot-token ...
mealprepper slack list-workspaces
mealprepper slack list-workspaces --households
mealprepper family add-slack-binding --slug ... --workspace-id ... --channel-id ...
mealprepper slack oauth-server
mealprepper slack authorize-url
```

## Security notes

- Treat `SLACK_BOT_TOKEN`, `SLACK_APP_TOKEN`, and `SLACK_CLIENT_SECRET` like passwords.
- Use a dedicated `#food` channel and set `SLACK_CHANNEL_ID` (or per-family `channel_id` in bindings).
- Only workspace members in that channel can trigger commands.
