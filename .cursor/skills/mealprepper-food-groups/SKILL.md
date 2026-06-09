---
name: mealprepper-food-groups
description: Ensure toddler and adult meals note balanced food groups (carb, protein, veggie, fruit, fat) in MealPrepper. Use when editing food_groups skill, meal_finder prompts, toddler meal sides, or food_groups.yaml.
---

# MealPrepper Food Groups

## Purpose

Toddler meals should cover **carb, protein, veggie, fruit, fat**. Adult meals get the same mapping when practical; infant BLW and bulk prep skip this.

## Implementation

- Skill: `src/mealprepper/skills/food_groups.py`
- Config: `config/food_groups.yaml`
- Model field: `MealRecipe.food_groups: dict[str, str]`
- Wired in: `MealFinderSkill._apply_block_safety()` after BLW/toddler checks

## Workflow

1. Classify ingredients (and title) against YAML keywords
2. Build `food_groups` map on the recipe
3. For toddler blocks with gaps → append `Food groups — … Add: …` to `toddler_modifications`
4. Render in playbook markdown

## Example toddler fix

Main: turkey + tortilla + cheese → missing veggie, fruit, fat sides.

Suggested additions from config: cucumber or carrot sticks (veggie), apple slices or banana (fruit), avocado or cheese cubes (fat).

## Tests

```bash
pytest tests/test_food_groups.py -q
```
