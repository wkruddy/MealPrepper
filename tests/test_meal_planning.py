from __future__ import annotations

from datetime import date

from mealprepper.skills.blw_safety import BLWSafety
from mealprepper.skills.meal_blocks import WeekMealOutline
from mealprepper.skills.meal_catalog import MealCatalog
from mealprepper.skills.week_outline import (
    enforce_variety,
    ensure_minimum_variety,
    fill_missing_slots,
    finalize_outline,
    normalize_block,
    normalize_day,
    parse_outline_items,
    sanitize_outlines,
)
from mealprepper.models.family import FamilyProfile


def test_normalize_day_from_iso():
    week_start = date(2026, 6, 2)  # Monday
    assert normalize_day("2026-06-04", week_start) == "wednesday"
    assert normalize_day("monday", week_start) == "monday"


def test_normalize_block_aliases():
    assert normalize_block("infant blw") == "infant_blw"
    assert normalize_block("adult_dinner") == "adult_dinner"


def test_parse_outline_items_skips_invalid():
    week_start = date(2026, 6, 2)
    raw = [
        {"day": "2026-06-02", "meal_block": "adult_dinner", "title": "Salmon", "key_ingredients": ["salmon"]},
        {"day": "2026-06-02", "title": "Missing block"},
    ]
    outlines = parse_outline_items(raw, week_start)
    assert len(outlines) == 1
    assert outlines[0].meal_block == "adult_dinner"
    assert outlines[0].day == "monday"


def test_catalog_fallback_has_variety():
    catalog = MealCatalog()
    outlines = catalog.build_fallback_outline(date(2026, 6, 2), max_repeat=2)
    dinners = [o.title for o in outlines if o.meal_block == "adult_dinner"]
    assert len(set(dinners)) >= 3
    assert max(dinners.count(title) for title in set(dinners)) <= 2


def test_catalog_maps_blocks_correctly():
    catalog = MealCatalog()
    outlines = catalog.build_fallback_outline(date(2026, 6, 2), max_repeat=2)
    monday_lunch = next(o for o in outlines if o.day == "monday" and o.meal_block == "toddler_school_lunch")
    monday_dinner = next(o for o in outlines if o.day == "monday" and o.meal_block == "adult_dinner")
    assert "roll" in monday_lunch.title.lower() or "pinwheel" in monday_lunch.title.lower() or "bento" in monday_lunch.title.lower() or "quesadilla" in monday_lunch.title.lower()
    assert "chicken" in monday_dinner.title.lower() or "salmon" in monday_dinner.title.lower() or "taco" in monday_dinner.title.lower() or "pasta" in monday_dinner.title.lower() or "beef" in monday_dinner.title.lower()


def test_enforce_variety_limits_repeats():
    catalog = MealCatalog()
    week_start = date(2026, 6, 2)
    repeated = [
        WeekMealOutline(day=day, meal_block="adult_dinner", title="Same Dinner", key_ingredients=["chicken"])
        for day in ["monday", "tuesday", "wednesday", "thursday", "friday"]
    ]
    adjusted = enforce_variety(repeated, catalog, max_repeat=2)
    titles = [o.title for o in adjusted]
    assert titles.count("Same Dinner") <= 2


def test_blw_blocks_honey():
    family = FamilyProfile.from_config(
        {
            "members": [
                {"id": "infant", "name": "Infant", "role": "infant", "age_months": 7.5, "constraints": []}
            ]
        }
    )
    blw = BLWSafety(family)
    warnings, blocked, guidance = blw.validate_meal("Honey Oats", ["oats", "honey"])
    assert "honey" in blocked
    assert "Do not serve" in guidance or "Replace" in guidance


def test_sanitize_drops_bulk_prep_on_wrong_day():
    outlines = [
        WeekMealOutline(
            day="monday",
            meal_block="bulk_meal_prep",
            title="Batch Rice",
            key_ingredients=["rice"],
        ),
        WeekMealOutline(
            day="monday",
            meal_block="adult_dinner",
            title="Salmon",
            key_ingredients=["salmon"],
        ),
    ]
    cleaned = sanitize_outlines(outlines)
    assert all(o.meal_block != "bulk_meal_prep" or o.day == "saturday" for o in cleaned)
    assert len(cleaned) == 1


def test_finalize_outline_survives_invalid_bulk_prep():
    catalog = MealCatalog()
    week_start = date(2026, 6, 2)
    outlines = parse_outline_items(
        [
            {
                "day": "monday",
                "meal_block": "bulk_meal_prep",
                "title": "Batch Rice",
                "key_ingredients": ["rice"],
            },
            {
                "day": "monday",
                "meal_block": "adult_dinner",
                "title": "Salmon",
                "key_ingredients": ["salmon"],
            },
        ],
        week_start,
    )
    result = finalize_outline(outlines, week_start, catalog, max_repeat=2)
    assert len(result) == 43
    bulk = [o for o in result if o.meal_block == "bulk_meal_prep"]
    assert len(bulk) == 1
    assert bulk[0].day == "saturday"


def test_ensure_minimum_variety_swaps_repeats():
    catalog = MealCatalog()
    repeated = [
        WeekMealOutline(day=day, meal_block="adult_dinner", title="Same Dinner", key_ingredients=["x"])
        for day in ["monday", "tuesday", "wednesday", "thursday", "friday"]
    ]
    adjusted = ensure_minimum_variety(repeated, catalog, min_unique=3)
    titles = [o.title for o in adjusted]
    assert len(set(titles)) >= 3


def test_fill_missing_slots():
    catalog = MealCatalog()
    week_start = date(2026, 6, 2)
    partial = [
        WeekMealOutline(
            day="monday",
            meal_block="adult_dinner",
            title="Salmon",
            key_ingredients=["salmon"],
        )
    ]
    filled = fill_missing_slots(partial, week_start, catalog, max_repeat=2)
    assert len(filled) == 43
