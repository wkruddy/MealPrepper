---
name: mealprepper-grocery
description: Build consolidated grocery lists for MealPrepper from approved weekly plans. Use when editing grocery_builder, inventory skills, or the Grocery List Agent.
---

# MealPrepper Grocery List Agent

## Role

Build shopping lists ready by **Sunday AM**, maximizing ingredient overlap with the weekly meal plan.

## Key files

- Agent: `src/mealprepper/agents/grocery.py`
- Skills: `src/mealprepper/skills/grocery_builder.py`, `inventory.py`, `ingredient_synergy.py`
- Models: `src/mealprepper/models/grocery.py`

## Workflow

1. Load latest **approved** weekly plan
2. Consolidate ingredients via `IngredientSynergySkill`
3. `GroceryBuilderSkill.build()` — LLM categorizes & quantities items
4. `InventorySkill.subtract_from_grocery()` — skip pantry items
5. Save to SQLite + markdown in `data/grocery/`

## Categories

`produce`, `dairy`, `meat`, `pantry`, `frozen`, `spices`, `other`

## CLI

```bash
python -m mealprepper generate-grocery
python -m mealprepper generate-grocery --plan-id <uuid>
```

## Inventory (future-ready)

```python
from mealprepper.skills.inventory import InventorySkill
inv = InventorySkill()
inv.add_item("olive oil", "750ml", "pantry")
```

When adding spice tracking, extend `inventory` table usage — do not break subtract logic.
