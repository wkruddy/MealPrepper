---
name: grocery-agent
description: Guide for the Grocery List Agent — generate-grocery, inventory, consolidation. Use when editing grocery_builder, inventory skill, or generate-grocery CLI.
---

# Grocery List Agent

## Role

Build consolidated shopping lists from approved weekly plans, subtract pantry inventory, render markdown.

## Key files

- `src/mealprepper/agents/grocery.py` — agent + `generate()`
- `src/mealprepper/skills/grocery_builder.py` — LLM list + text render
- `src/mealprepper/skills/inventory.py` — pantry subtraction
- `src/mealprepper/skills/ingredient_synergy.py` — pre-consolidation

## CLI

```bash
mealprepper approve-plan          # if plan pending
mealprepper generate-grocery [--plan-id ID]
```

## Flow

1. Load latest approved plan (or specified ID)
2. Consolidate ingredients via synergy skill
3. Build categorized grocery list (LLM or fallback dedupe)
4. Subtract items already in `inventory` table
5. Save to SQLite + write markdown to `data/`

## Sunday schedule

Configured in `config/family.yaml`: `grocery_ready_day: sunday`, `grocery_ready_time: "08:00"`
