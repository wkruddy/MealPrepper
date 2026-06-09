from __future__ import annotations

from datetime import date

from mealprepper.models.meals import MealRecipe, PlannedMeal
from mealprepper.models.plans import PlanStatus, WeeklyPlan
from mealprepper.skills.dish_history import DishHistorySkill, normalize_title
from mealprepper.skills.meal_blocks import WeekMealOutline
from mealprepper.skills.meal_catalog import MealCatalog
from mealprepper.skills.week_outline import enforce_cross_week_exclusion
from mealprepper.storage.sqlite import SQLiteStore


def test_normalize_title():
    assert normalize_title("  Chicken Tacos  ") == "chicken tacos"


def test_recent_dishes_by_block_lookback(tmp_path):
    db_path = tmp_path / "test.db"
    store = SQLiteStore(db_path=db_path)

    prev_week = date(2026, 5, 26)
    prev_plan = WeeklyPlan(
        week_start=prev_week,
        week_end=date(2026, 6, 1),
        status=PlanStatus.APPROVED,
        meals=[
            PlannedMeal(
                day="monday",
                meal_block="adult_dinner",
                recipe=MealRecipe(title="Salmon Bowl"),
            ),
            PlannedMeal(
                day="tuesday",
                meal_block="adult_dinner",
                recipe=MealRecipe(title="Chicken Tacos"),
            ),
        ],
    )
    store.save_weekly_plan(prev_plan)

    current_week = date(2026, 6, 2)
    dishes = store.recent_dishes_by_block(current_week, lookback_weeks=2)
    assert "Salmon Bowl" in dishes["adult_dinner"]
    assert "Chicken Tacos" in dishes["adult_dinner"]


def test_dish_history_format_exclusions():
    skill = DishHistorySkill()
    text = skill.format_exclusions({"adult_dinner": {"Salmon Bowl", "Chicken Tacos"}})
    assert "Salmon Bowl" in text
    assert "Chicken Tacos" in text
    assert "do not repeat" in text.lower()


def test_enforce_cross_week_exclusion_swaps_recent_dish():
    catalog = MealCatalog()
    excluded = {"adult_dinner": {normalize_title("Same Dinner")}}
    outlines = [
        WeekMealOutline(
            day=day,
            meal_block="adult_dinner",
            title="Same Dinner",
            key_ingredients=["chicken"],
        )
        for day in ["monday", "tuesday", "wednesday"]
    ]
    adjusted = enforce_cross_week_exclusion(outlines, catalog, excluded, max_repeat=2)
    titles = [o.title for o in adjusted]
    assert titles.count("Same Dinner") < len(outlines)
