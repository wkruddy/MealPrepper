---
name: mealprepper-weekly-meals
description: Orchestrate weekly family meal planning for MealPrepper — find meals, organize the week, synergize ingredients, track preferences. Use when planning meals, editing meal_finder/week_organizer skills, or working on the Weekly Meals Agent.
---

# MealPrepper Weekly Meals Agent

## Role

Orchestrate weekly meal planning for a family with toddler (3), infant BLW (7.5mo), and 2 adults.

## Key files

- Agent: `src/mealprepper/agents/weekly_meals.py`
- Skills: `src/mealprepper/skills/meal_finder.py`, `week_organizer.py`, `ingredient_synergy.py`, `food_groups.py`
- Models: `src/mealprepper/models/meals.py`, `plans.py`
- Config: `config/family.yaml`

## Constraints (always enforce)

- Toddler: no spicy; dinner by 5:30pm; max 45min total prep+cook; quick lunches; all five food groups
- Infant: BLW finger foods with safe-size prep guidance
- Adults: variable breakfast; bulk-preppable lunches; shared dinners with toddler
- Minimize wasted ingredients — overlap produce/proteins across meals

## Meal blocks (cover all daily)

- Weekdays: `toddler_school_lunch`, `toddler_breakfast`, `adult_breakfast`, `adult_lunch`, `adult_dinner`, `infant_blw`
- Weekends: swap `toddler_weekend_lunch`; optional `bulk_meal_prep` Saturday

## Workflow

1. Load preferences from SQLite (`storage/sqlite.py`)
2. `MealFinderSkill.find_week_outline()` → expand recipes
3. `WeekOrganizerSkill.organize_week()` → playbook markdown
4. `IngredientSynergySkill.analyze()` → waste reduction notes
5. Save plan with status `pending_approval`

## CLI

```bash
python -m mealprepper plan-week
python -m mealprepper plan-week --auto-approve
```

## LLM

Uses Ollama OpenAI-compatible API. Falls back to template meals if Ollama unavailable.

When modifying prompts, keep JSON output schemas aligned with Pydantic models in `models/meals.py`.
