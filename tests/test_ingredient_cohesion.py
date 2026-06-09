from mealprepper.skills.ingredient_cohesion import (
    align_bulk_prep_to_anchors,
    compute_anchor_ingredients,
)
from mealprepper.skills.meal_blocks import WeekMealOutline


def _outline(day: str, block: str, title: str, ingredients: list[str]) -> WeekMealOutline:
    return WeekMealOutline(
        day=day,
        meal_block=block,
        title=title,
        key_ingredients=ingredients,
        prep_minutes=15,
    )


def test_compute_anchor_ingredients_finds_shared_items():
    outlines = [
        _outline("monday", "adult_dinner", "Tacos", ["rice", "chicken", "cilantro"]),
        _outline("tuesday", "adult_dinner", "Bowls", ["rice", "broccoli", "chicken"]),
        _outline("saturday", "bulk_meal_prep", "Batch cook", ["rice"]),
    ]
    anchors = compute_anchor_ingredients(outlines, min_mentions=2)
    assert "rice" in anchors
    assert "chicken" in anchors


def test_align_bulk_prep_adds_missing_anchors():
    outlines = [
        _outline("monday", "adult_dinner", "Tacos", ["rice", "chicken"]),
        _outline("tuesday", "adult_dinner", "Bowls", ["rice", "chicken"]),
        _outline("saturday", "bulk_meal_prep", "Batch cook", ["quinoa"]),
    ]
    aligned = align_bulk_prep_to_anchors(outlines, ["rice", "chicken"])
    bulk = next(item for item in aligned if item.meal_block == "bulk_meal_prep")
    joined = " ".join(bulk.key_ingredients).lower()
    assert "rice" in joined
    assert "chicken" in joined
