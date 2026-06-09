---
name: weekly-meals-agent
description: Guide for the Weekly Meals Agent — plan-week, family constraints, ingredient synergy. Use when editing weekly meal planning, meal_finder, week_organizer, or plan-week CLI.
---

# Weekly Meals Agent

## Role

Find age-appropriate meals, organize the week across all meal blocks, synergize ingredients, and persist plans.

## Key files

- `src/mealprepper/agents/weekly_meals.py` — agent + `plan_week()`
- `src/mealprepper/skills/meal_finder.py` — LLM meal outline + recipe expansion
- `src/mealprepper/skills/food_groups.py` — toddler food group balance
- `src/mealprepper/skills/week_organizer.py` — weekly structure + playbook
- `src/mealprepper/skills/ingredient_synergy.py` — overlap analysis
- `config/family.yaml` — member constraints and meal blocks

## CLI

```bash
mealprepper plan-week [--week-start YYYY-MM-DD] [--auto-approve]
mealprepper show-plan [--markdown]
```

## Flow

1. Load preferences from SQLite
2. `organize_week` → outlines + recipes per block/day
3. `synergize_ingredients` → notes on shared produce
4. Save with status `pending_approval` (unless auto-approve)

## Constraints (from family.yaml)

- Toddler: no spicy, dinner by 17:30, max 45min prep
- Infant: BLW finger foods with prep guidance
- Adults: variable breakfast, bulk-prep lunches, shared dinners

## LLM

Local Ollama only. Falls back to template meals when unavailable.
