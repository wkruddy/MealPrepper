from __future__ import annotations

import logging
from collections import Counter

from mealprepper.skills.meal_blocks import DAYS, WeekMealOutline
from mealprepper.skills.pantry_config import _normalize_name

logger = logging.getLogger(__name__)

COHESION_BLOCKS = {"adult_dinner", "adult_lunch", "bulk_meal_prep", "toddler_school_lunch"}


def compute_anchor_ingredients(
    outlines: list[WeekMealOutline],
    *,
    top_n: int = 10,
    min_mentions: int = 2,
) -> list[str]:
    """Ingredients that appear across multiple meals — reuse targets for the week."""
    counts: Counter[str] = Counter()
    for outline in outlines:
        if outline.meal_block not in COHESION_BLOCKS:
            continue
        for raw in outline.key_ingredients:
            token = _normalize_name(str(raw))
            if len(token) >= 3 and not token.isdigit():
                counts[token] += 1

    anchors = [name for name, count in counts.most_common(top_n) if count >= min_mentions]
    if anchors:
        logger.info("Week anchor ingredients: %s", ", ".join(anchors[:8]))
    return anchors


def align_bulk_prep_to_anchors(
    outlines: list[WeekMealOutline],
    anchors: list[str],
) -> list[WeekMealOutline]:
    """Ensure Saturday bulk prep cooks components reused later in the week."""
    if not anchors:
        return outlines

    updated: list[WeekMealOutline] = []
    for outline in outlines:
        if outline.meal_block != "bulk_meal_prep":
            updated.append(outline)
            continue
        existing = {_normalize_name(item) for item in outline.key_ingredients}
        merged = list(outline.key_ingredients)
        for anchor in anchors[:4]:
            if anchor not in existing:
                merged.append(anchor.title())
        copy = outline.model_copy()
        copy.key_ingredients = merged[:8]
        if merged != list(outline.key_ingredients):
            copy.cook_note = (copy.cook_note or "") + " Batch cook for weekday reuse."
        updated.append(copy)
    return updated


def cohesion_prompt_lines(anchors: list[str]) -> str:
    if not anchors:
        return ""
    joined = ", ".join(anchor.title() for anchor in anchors[:8])
    return (
        f"Week anchor ingredients (reuse across meals): {joined}.\n"
        "- Cook extra rice/grains/roasted veg when a dinner uses them; reuse next day.\n"
        "- Prefer dinners that share at least 2 anchor ingredients with another night.\n"
        "- Saturday bulk_meal_prep should batch-cook anchors for Mon–Thu."
    )
