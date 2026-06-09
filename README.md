# MealPrepper

Family meal planning with local Ollama agents. Plans weekly meals for toddler, infant (BLW), and adults; builds grocery lists; sends plan summaries via Slack, Discord, Telegram, or iMessage.

## Requirements

- Python 3.11+
- [Ollama](https://ollama.com/) running locally (`ollama serve`)
- A pulled model (default: `llama3.2`)

## Setup

```bash
cd MealPrepper
cp .env.example .env          # optional — defaults work for dev
pip install -e ".[dev]"

# Pull model (if not already)
ollama pull qwen3:8b

# Initialize database
mealprepper init-db
```

Family constraints live in `config/family.yaml`. App defaults in `config/default.yaml`.
Meal templates and BLW safety rules live in `config/meal_catalog.yaml` and `config/blw.yaml`.
Edit `config/pantry.yaml` for spices and staples you already keep on hand — they won't appear on the shopping list.

### Pantry config (`config/pantry.yaml`)

The grocery list is split into three sections:

1. **Shop for recipes** — unique or recipe-specific items (salmon, feta, deli turkey, etc.) with shoppable quantities (e.g. "2 lb" not "1 portion")
2. **Weekly staples** — common items you buy most weeks (milk, eggs, bread) listed separately as "buy if low"
3. **Already in pantry** — spices, oils, and pantry items you keep stocked; excluded from shopping

Edit the YAML to match your kitchen:

```yaml
on_hand:
  spices:
    - salt
    - cinnamon
  oils_and_condiments:
    - olive oil
  pantry:
    - rice
    - pasta

weekly_staples:
  - milk
  - eggs
  - bread
```

Similar ingredient names are merged automatically (e.g. "Greek yogurt" + "yogurt" → one line). Vague recipe quantities like "1 portion" are converted to sensible buy amounts using a built-in lookup table.

### Cook efficiency (`config/default.yaml`)

Meal planning prioritizes **fewer cook sessions** over maximum variety when `cook_efficiency.enabled` is true:

- **~4 unique adult dinners** per week; other nights repeat those meals
- **Monday dinner → Tuesday lunch** leftovers (same recipe, no second cook)
- Saturday `bulk_meal_prep` should align with weekday components

Verify reuse after planning:

```bash
mealprepper show-plan -t -s          # titles + synergy inline
mealprepper show-synergy             # full cook-efficiency report
```

Tune in `config/default.yaml` under `planning.cook_efficiency` (`max_dinner_cook_sessions`, `cross_block_reuse`, etc.).

### Food shelf life (`config/food_shelf_life.yaml`)

During planning, MealPrepper checks how long cooked food realistically keeps in the fridge before reuse:

- **Seafood** (~2 days) — won't schedule Monday salmon as Friday leftovers
- **Poultry** (~3 days), **red meat** (~4 days), **vegetarian/grain** (~5 days)

Verify leftover timing:

```bash
mealprepper show-shelf-life
mealprepper show-synergy              # includes shelf-life section
```

Edit category keywords and `fridge_days` in `config/food_shelf_life.yaml` to match your comfort level.

### Family recipe library

Build a searchable library of recipes and meal ideas your family already likes. MealPrepper uses this during `plan-week` for inspiration and can reuse full saved recipes instead of generating from scratch.

**Import one-off** (writes directly to the SQLite recipe library — used automatically during `plan-week`):

```bash
mealprepper import-recipe --text "Mild turkey tacos — kids love with avocado"
mealprepper import-recipe --title "Beef Stir Fry" --file ../ai-data/mealprepper/recipes/my-recipe.md
mealprepper import-recipe --url "https://example.com/recipe-page"
```

Some recipe sites (Allrecipes, etc.) block automated downloads with HTTP 403. For those, copy the recipe into a file or use `--text` / `--file` instead.

**Bulk import from config** (also writes to the same DB — run when you add or change sources, not every plan):

Edit `config/recipe_sources.yaml`, then:

```bash
mealprepper sync-recipes
mealprepper list-recipes
mealprepper list-recipes -q chicken
```

There is no separate “planning sync” step. Once a recipe is in the DB, `plan-week` searches it via the recipe index. `sync-recipes` is just a batch version of `import-recipe` driven by YAML.

**Trello workflow:** Export your board as JSON, then convert it into structured sources (markdown files, URLs, and text ideas):

```bash
python scripts/trello_to_recipe_sources.py ../ai-data/mealprepper/trello-export.json
mealprepper sync-recipes
```

The script classifies each card: full steps in the description → markdown file; attachment URL → `url` source; name-only → `text` idea. Avoid `--trello-export` for large boards — it dumps every card through the LLM parser and is slow.

Card **names** become titles; **descriptions** become recipe text/notes.

Markdown files support optional front matter:

```markdown
# Recipe Title
blocks: adult_dinner
tags: family-favorite

## Ingredients
- chicken — 2 lb

## Steps
1. Cook and serve.
```

During planning, saved recipes appear in the meal-finder prompt as "Family recipe library". Exact title matches use your saved full recipe (with steps) automatically.

## Notifications

MealPrepper sends **weekly plan approvals** and **daily meal summaries** through a pluggable comms backend — no SMS or paid Twilio required.

Set `COMMS_BACKEND` in `.env` (legacy alias: `SMS_BACKEND`):

| Backend | Best for | Setup |
|---------|----------|-------|
| `console` | Dev / VM testing | Default — prints to terminal |
| `slack` | Daily Slack users (recommended) | Incoming webhook URL |
| `discord` | Discord households | Channel webhook URL |
| `telegram` | Phone push via BotFather | Bot token + chat id |
| `imessage` | macOS with Shortcuts relay | Apple Shortcuts webhook → Messages |

### Slack setup (recommended)

**Full guide:** [docs/SLACK_BOT.md](docs/SLACK_BOT.md)

1. Create a Slack app → enable **Socket Mode** + **Incoming Webhooks**.
2. Add bot scopes: `chat:write`, `channels:history`, `groups:history`, `app_mentions:read`, `commands`.
3. Subscribe to bot events: `message.channels`, `app_mention`.
4. Create slash command `/mealprepper`.
5. Install to workspace; invite `@MealPrepper` to `#food`.

```bash
COMMS_BACKEND=slack
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...   # outbound
SLACK_BOT_TOKEN=xoxb-...                                  # inbound bot
SLACK_APP_TOKEN=xapp-...                                  # Socket Mode
SLACK_CHANNEL_ID=C0123456789                              # your channel

pip install -e ".[slack]"

# Run the inbound bot as a background service (no terminal needed):
./scripts/install_systemd.sh
systemctl --user enable --now mealprepper-watch-messages.service

# Or run in the foreground for debugging only:
mealprepper watch-messages
```

**Bot commands:** `approve`, `reject`, `status`, `plan`, `daily`, `grocery`, `help`, `loved tacos` — or `/mealprepper approve`.

### Slack / Discord / Telegram / iMessage

```bash
# Slack
COMMS_BACKEND=slack
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T.../B.../...
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
SLACK_CHANNEL_ID=C0123456789

# Discord
COMMS_BACKEND=discord
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...

# Telegram (when you're ready — create bot via @BotFather)
COMMS_BACKEND=telegram
TELEGRAM_BOT_TOKEN=123456:ABC...
TELEGRAM_CHAT_ID=your_chat_id

# macOS iMessage via Shortcuts
COMMS_BACKEND=imessage
APPLE_SHORTCUTS_WEBHOOK_URL=https://...
```

## Weekly Workflow

| Day | Time | Command | What happens |
|-----|------|---------|--------------|
| Saturday | 10:00 | `mealprepper plan-week` | Weekly Meals Agent builds plan, posts approval to Slack/etc. |
| — | — | `approve` in Slack (or `mealprepper process-feedback -m APPROVE`) | Approve pending plan |
| Sunday | 08:00 | `mealprepper generate-grocery` | Grocery Agent builds shopping list |
| Daily | 07:00 | `mealprepper send-daily` | Morning notification with today's meals |

### First plan-week

```bash
mealprepper init-db
mealprepper plan-week --auto-approve   # skip approval notification for first run
mealprepper show-plan --markdown
mealprepper show-plan --titles-only   # quick scan: meal names only
mealprepper show-plan --recipes       # full step-by-step recipes
mealprepper show-shelf-life           # leftover timing check
mealprepper generate-grocery
mealprepper show-grocery --markdown
```

Without `--auto-approve`, the plan stays `pending_approval` until you run:

```bash
mealprepper process-feedback -m "APPROVE"
# or
mealprepper approve-plan
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `init-db` | Create SQLite schema in `data/mealprepper.db` |
| `plan-week [--week-start YYYY-MM-DD] [--auto-approve]` | Generate weekly meal plan |
| `approve-plan [--plan-id ID]` | Manually approve pending plan |
| `generate-grocery [--plan-id ID]` | Build grocery list from approved plan |
| `import-recipe [--text\|--url\|--file\|--trello-export]` | Add a family recipe or meal idea to the library |
| `sync-recipes` | Import all sources from `config/recipe_sources.yaml` |
| `purge-recipes --duplicates` | Remove duplicate saved recipes (keeps best copy per title) |
| `list-recipes [--query Q]` | Browse/search saved family recipes |
| `show-grocery [--plan-id ID] [--markdown]` | Display latest grocery list (recipe items, weekly staples, pantry assumed) |
| `send-daily [--date YYYY-MM-DD]` | Send morning meal summary notification |
| `process-feedback [-m MESSAGE]` | Apply pending feedback or parse inbound message |
| `show-plan [--plan-id ID] [--markdown] [--titles-only] [--recipes] [--synergy]` | Display plan, titles, full recipes, or synergy report |
| `show-synergy [--plan-id ID] [--markdown]` | Cook reuse, shared ingredients, shelf life, synergy notes |
| `show-shelf-life [--plan-id ID]` | Leftover timing rules and food-safety reuse issues |
| `watch-messages` | Run Slack bot listener (Socket Mode; requires `[slack]` extra) |

Also: `python -m mealprepper <command>`

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama API URL |
| `OLLAMA_MODEL` | `llama3.2` | Model name — use a **fast** model for `plan-week` (40+ LLM calls) |
| `MEALPREPPER_DATA_DIR` | `./data` | SQLite + output files |
| `COMMS_BACKEND` | `console` | `console`, `slack`, `discord`, `telegram`, `imessage` (alias: `SMS_BACKEND`) |
| `SLACK_WEBHOOK_URL` | — | Slack incoming webhook (outbound notifications) |
| `SLACK_BOT_TOKEN` | — | Bot User OAuth Token (`xoxb-...`) for inbound |
| `SLACK_APP_TOKEN` | — | App-Level Token (`xapp-...`, scope `connections:write`) |
| `SLACK_CHANNEL_ID` | — | Restrict bot to one channel (recommended) |
| `DISCORD_WEBHOOK_URL` | — | Discord channel webhook |
| `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` | — | Telegram BotFather bot |
| `APPLE_SHORTCUTS_WEBHOOK_URL` | — | macOS Shortcuts → iMessage relay |
| `APPROVAL_REQUIRED` | `true` | Send approval notification after planning |
| `DAILY_REMINDER_HOUR` | `7` | Used by systemd/cron/launchd scheduling |

On your Linux VM, use `COMMS_BACKEND=slack` with a webhook URL — free, no phone carrier needed. iMessage requires a Mac running Shortcuts as a relay.

## Scheduling

Weekly jobs run automatically once installed. Pick the method for your OS:

### Linux (systemd — recommended)

```bash
./scripts/install_systemd.sh
```

Installs user systemd timers for Saturday plan (10:00), Sunday grocery (08:00), and daily notifications (07:00). Also writes `mealprepper-watch-messages.service` for the always-on Slack bot. Logs go to `data/logs/`.

Enable the Slack bot listener (runs in background, restarts on failure):

```bash
systemctl --user enable --now mealprepper-watch-messages.service
systemctl --user status mealprepper-watch-messages.service
tail -f data/logs/watch-messages.log
```

Stop the foreground `mealprepper watch-messages` if you started one manually — only one listener should run.

If timers and the bot should run while you are logged out:

```bash
sudo loginctl enable-linger $USER
```

Check status: `systemctl --user list-timers 'mealprepper-*'`

### macOS (launchd)

```bash
./scripts/install_launchd.sh
```

Installs launchd plists under `~/Library/LaunchAgents/`. See `scripts/com.mealprepper.plan-week.plist.example`.

### Any platform (cron)

```bash
chmod +x scripts/cron/run_scheduled.sh
crontab -e
```

```cron
0 10 * * 6  /path/to/MealPrepper/scripts/cron/run_scheduled.sh weekly-plan >> /path/to/MealPrepper/data/logs/plan-week.log 2>&1
0  8 * * 0  /path/to/MealPrepper/scripts/cron/run_scheduled.sh grocery >> /path/to/MealPrepper/data/logs/generate-grocery.log 2>&1
0  7 * * *  /path/to/MealPrepper/scripts/cron/run_scheduled.sh daily >> /path/to/MealPrepper/data/logs/send-daily.log 2>&1
```

Auto-detect OS: `./scripts/install_scheduler.sh`

## Development

```bash
pytest
ruff check src tests
```

Agents fall back to template meals when Ollama is unavailable — tests run without a live LLM.

## Context & Performance

MealPrepper keeps LLM prompts small by **retrieving** only relevant history instead of dumping full preference profiles or past plans.

### How it works

1. **SQLite FTS5 indexes** — Saved meals, feedback, and weekly plans are indexed on write (`meal_index`, `feedback_index`, `plan_index` + FTS virtual tables).
2. **Top-k retrieval** — `MealFinderSkill` pulls recent/similar meals and block-specific feedback via `MealIndex` and `PreferenceIndex`; `PlanIndex` adds compact past-week summaries.
3. **PromptBuilder + ContextBudget** — Sections are assembled by priority; lower-priority chunks are trimmed or dropped when over budget.
4. **Compression** — `ContextCompressor` caps list lengths and merges feedback into rolling `preference_summaries` stored in SQLite.
5. **Today-only slices** — Communications and daily notifications use `WeeklyPlan.meals_for_day()` so only the target day's meals are formatted, not the full week.

### Config knobs (`config/default.yaml`)

| Key | Default | Description |
|-----|---------|-------------|
| `context.max_context_chars` | `12000` | Global fallback char limit for LLM prompts |
| `context.budgets.*` | per call-type | Overrides per agent/skill (`meal_finder`, `recipe_expand`, `week_organizer`, `grocery`, `comms`, `preference`) |
| `context.warn_at_pct` | `0.85` | Log a warning when prompt size exceeds this fraction of the budget |
| `index.meal_top_k` | `5` | Past meals retrieved for meal planning |
| `index.feedback_top_k` | `8` | Feedback entries retrieved per meal block |
| `index.plan_top_k` | `2` | Past week summaries included in prompts |
| `index.use_embeddings` | `false` | Optional semantic search via `OllamaClient.embed()` (FTS is default) |
| `MAX_CONTEXT_CHARS` (env) | `12000` | Overrides `context.max_context_chars` |
| `OLLAMA_EMBEDDING_MODEL` (env) | `nomic-embed-text` | Model used when embeddings are enabled |

Run `mealprepper init-db` to create index tables and FTS migrations on existing databases.

### Tuning for smaller models (e.g. `llama3.2:3b`)

Edit `config/default.yaml`:

```yaml
context:
  max_context_chars: 6000      # global fallback
  budgets:
    meal_finder: 5000          # week outline generation
    recipe_expand: 2500        # single recipe expansion
    comms: 2000                # notification formatting
  warn_at_pct: 0.80            # log when prompt reaches 80% of budget

index:
  meal_top_k: 3                # fewer past meals in prompt
  feedback_top_k: 5
  plan_top_k: 1
  use_embeddings: false        # FTS only; set true + Ollama embed model for semantic search
```

Environment overrides: `MAX_CONTEXT_CHARS`, `OLLAMA_EMBEDDING_MODEL` (default `nomic-embed-text`).

### Expected context reduction

| Prompt | Before | After (typical) |
|--------|--------|-----------------|
| Meal finder preferences | Full liked/disliked lists + all notes | ~15 items + 500-char summary (~400–600 tokens) |
| Meal finder history | N/A (no retrieval) | Top 5 indexed meals (~200 tokens) |
| Recipe expand | Full outline only | Outline + 2 similar past recipes (~300 tokens) |
| Daily notifications | Already day-scoped | Unchanged — uses `DailyPlanSummary` slice only |
| Preference learning | Raw comment append | Batch summary stored in `preference_summaries` |

For a family with 50+ feedback entries and multiple past plans, meal-finder prompts typically shrink from **8k–15k chars to ~3k–5k chars** (~60% reduction).
