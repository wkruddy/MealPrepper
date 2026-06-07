from datetime import date

from mealprepper.models.meals import Ingredient, MealRecipe, PlannedMeal
from mealprepper.models.plans import WeeklyPlan
from mealprepper.skills.playbook_renderer import PlaybookRendererSkill


def test_render_titles_only_shows_names_without_recipe_details():
    plan = WeeklyPlan(
        week_start=date(2025, 6, 2),
        week_end=date(2025, 6, 8),
        meals=[
            PlannedMeal(
                meal_block="adult_dinner",
                day="monday",
                recipe=MealRecipe(
                    title="Sheet Pan Chicken",
                    prep_minutes=15,
                    cook_minutes=30,
                    ingredients=[Ingredient(name="chicken", quantity="1", unit="lb")],
                ),
            ),
            PlannedMeal(
                meal_block="toddler_school_lunch",
                day="monday",
                recipe=MealRecipe(title="Turkey Roll-ups"),
            ),
        ],
    )
    text = PlaybookRendererSkill().render_titles_only(plan)
    assert "Sheet Pan Chicken" in text
    assert "Turkey Roll-ups" in text
    assert "Adult Dinner" in text
    assert "chicken" not in text
    assert "Prep:" not in text
    assert "Ingredients:" not in text
