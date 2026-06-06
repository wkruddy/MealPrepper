# MealPrepper

Family meal planning with local Ollama agents. Plans weekly meals for toddler, infant (BLW), and adults; builds grocery lists; sends SMS summaries.

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
ollama pull llama3.2

# Initialize database
mealprepper init-db
```

Family constraints live in `config/family.yaml`. App defaults in `config/default.yaml`.

## Weekly Workflow

| Day | Time | Command | What happens |
|-----|------|---------|--------------|
| Saturday | 10:00 | `mealprepper plan-week` | Weekly Meals Agent builds plan, sends approval SMS |
| — | — | `mealprepper process-feedback -m APPROVE` | Approve plan (or reply via SMS in production) |
| Sunday | 08:00 | `mealprepper generate-grocery` | Grocery Agent builds shopping list |
| Daily | 07:00 | `mealprepper send-daily` | Morning SMS with today's meals |

### First plan-week

```bash
mealprepper init-db
mealprepper plan-week --auto-approve   # skip SMS approval for first run
mealprepper show-plan --markdown
mealprepper generate-grocery
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
| `send-daily [--date YYYY-MM-DD]` | Send morning meal summary SMS |
| `process-feedback [-m MESSAGE]` | Apply pending feedback or parse inbound SMS |
| `show-plan [--plan-id ID] [--markdown]` | Display plan table or playbook |
| `watch-messages` | Stub for inbound SMS webhook |

Also: `python -m mealprepper <command>`

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama API URL |
| `OLLAMA_MODEL` | `llama3.2` | Model name — use a **fast** model for `plan-week` (40+ LLM calls) |
| `MEALPREPPER_DATA_DIR` | `./data` | SQLite + output files |
| `SMS_BACKEND` | `console` | `console` (dev), `twilio` (Linux/production), `apple_shortcuts` (macOS webhook), `imsg` (macOS stub) |
| `APPROVAL_REQUIRED` | `true` | Send approval SMS after planning |
| `DAILY_REMINDER_HOUR` | `7` | Used by systemd/cron/launchd scheduling |

Twilio vars: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`, `TWILIO_TO_NUMBER`

On Linux servers, use `SMS_BACKEND=twilio` for real SMS. `apple_shortcuts` and `imsg` are macOS-only integrations.

## Scheduling

Weekly jobs run automatically once installed. Pick the method for your OS:

### Linux (systemd — recommended)

```bash
./scripts/install_systemd.sh
```

Installs user systemd timers for Saturday plan (10:00), Sunday grocery (08:00), and daily SMS (07:00). Logs go to `data/logs/`.

If timers should run while you are logged out:

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
5. **Today-only slices** — Communications and daily SMS use `WeeklyPlan.meals_for_day()` so only the target day's meals are formatted, not the full week.

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
    comms: 2000                # SMS formatting
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
| Daily SMS | Already day-scoped | Unchanged — uses `DailyPlanSummary` slice only |
| Preference learning | Raw comment append | Batch summary stored in `preference_summaries` |

For a family with 50+ feedback entries and multiple past plans, meal-finder prompts typically shrink from **8k–15k chars to ~3k–5k chars** (~60% reduction).
