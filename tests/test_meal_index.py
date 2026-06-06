import tempfile
from datetime import date, timedelta
from pathlib import Path

from mealprepper.index.meal_index import MealIndex
from mealprepper.models.meals import Ingredient, MealRecipe, PlannedMeal
from mealprepper.storage.sqlite import SQLiteStore


def _make_meal(title: str, block: str, ingredients: list[str]) -> PlannedMeal:
    return PlannedMeal(
        meal_block=block,
        day="monday",
        recipe=MealRecipe(
            title=title,
            ingredients=[Ingredient(name=i) for i in ingredients],
            tags=["quick"],
        ),
    )


def test_meal_index_fts_search():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        store = SQLiteStore(db_path=db_path)
        index = MealIndex(db_path=db_path)

        plan_start = date.today()
        plan_end = plan_start + timedelta(days=6)
        from mealprepper.models.plans import WeeklyPlan, PlanStatus

        plan = WeeklyPlan(
            week_start=plan_start,
            week_end=plan_end,
            status=PlanStatus.DRAFT,
            meals=[
                _make_meal("Lemon Herb Chicken", "adult_dinner", ["chicken", "lemon", "broccoli"]),
                _make_meal("Overnight Oats", "adult_breakfast", ["oats", "yogurt", "berries"]),
                _make_meal("Turkey Roll-ups", "toddler_school_lunch", ["turkey", "cheese"]),
            ],
        )
        store.save_weekly_plan(plan)

        results = index.search("chicken lemon", meal_block="adult_dinner", top_k=3)
        assert len(results) >= 1
        assert any("Chicken" in r.title for r in results)

        breakfast = index.search("oats", meal_block="adult_breakfast", top_k=2)
        assert any("Oats" in r.title for r in breakfast)


def test_meal_index_format_for_prompt():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        index = MealIndex(db_path=db_path)
        index.index_meal(_make_meal("Pasta Primavera", "adult_dinner", ["pasta", "zucchini"]), plan_id="p1")
        results = index.search("pasta", top_k=1)
        text = index.format_for_prompt(results)
        assert "Pasta Primavera" in text
