---
name: meal-builder-agent
description: Guide for the Meal Builder Agent — meal_finder, recipe expansion, toddler food group balance. Use when editing meal_finder, food_groups skill, or toddler/adult meal composition.
---

# Meal Builder Agent

## Role

Build age-appropriate meals for each day/block: week outlines, full recipes, toddler safety notes, and balanced food group coverage.

## Key files

- `src/mealprepper/skills/meal_finder.py` — outline + recipe expansion (main meal builder)
- `src/mealprepper/skills/food_groups.py` — carb/protein/veggie/fruit/fat classification
- `config/food_groups.yaml` — group keywords and fallback side suggestions
- `src/mealprepper/models/meals.py` — `MealRecipe.food_groups` field

## Food group rules

**Toddler meals** (`toddler_*` blocks) — **enforce all five groups**:

- carb, protein, veggie, fruit, fat
- Include sides when the main dish lacks a group (e.g. apple slices, carrot sticks)
- `FoodGroupsSkill.annotate_recipe()` fills `food_groups` and appends notes to `toddler_modifications`

**Adult meals** (`adult_*` blocks) — **note only**:

- Populate `food_groups` when ingredients map clearly; no hard requirement

**Skip** for `infant_blw` and `bulk_meal_prep`.

## LLM prompts

`MealFinderSkill` injects food-group guidance into week outline and recipe expansion prompts. Recipe JSON includes:

```json
"food_groups": {
  "carb": "tortilla",
  "protein": "turkey",
  "veggie": "cucumber",
  "fruit": "apple",
  "fat": "cheese"
}
```

## Playbook output

`PlaybookRendererSkill` shows a **Food groups** line when present.

## Tuning

Edit keyword lists and fallback sides in `config/food_groups.yaml`.
