from datetime import date

from mealprepper.models.grocery import GroceryCategory, GroceryItem, GroceryList
from mealprepper.models.meals import Ingredient, MealRecipe, PlannedMeal
from mealprepper.models.plans import WeeklyPlan
from mealprepper.skills.grocery_builder import GroceryBuilderSkill


def _plan_with_meals() -> WeeklyPlan:
    return WeeklyPlan(
        id="test-plan",
        week_start=date(2025, 6, 2),
        week_end=date(2025, 6, 8),
        meals=[
            PlannedMeal(
                meal_block="adult_dinner",
                day="monday",
                recipe=MealRecipe(
                    title="Sheet Pan Chicken",
                    ingredients=[
                        Ingredient(name="chicken thighs", quantity="2", unit="lb", category="meat"),
                        Ingredient(name="broccoli", quantity="1", unit="head", category="produce"),
                    ],
                ),
            ),
            PlannedMeal(
                meal_block="adult_lunch",
                day="tuesday",
                recipe=MealRecipe(
                    title="Grain Bowl",
                    ingredients=[
                        Ingredient(name="broccoli", quantity="2", unit="cups", category="produce"),
                        Ingredient(name="quinoa", quantity="1", unit="cup", category="pantry"),
                    ],
                ),
            ),
        ],
    )


def test_build_grocery_list_fallback_without_ollama():
    builder = GroceryBuilderSkill()
    grocery = builder.build(_plan_with_meals())
    assert grocery.weekly_plan_id == "test-plan"
    assert len(grocery.items) >= 3
    assert grocery.ready_for_shopping


def test_render_text_groups_by_category():
    builder = GroceryBuilderSkill()
    grocery = GroceryList(
        week_label="2025-06-02 — 2025-06-08",
        items=[
            GroceryItem(
                name="broccoli",
                quantity="1",
                category=GroceryCategory.PRODUCE,
            ),
        ],
    )
    text = builder.render_text(grocery)
    assert "Grocery List" in text
    assert "broccoli" in text
    assert "Produce" in text


def test_from_ingredients_dedupes():
    ingredients = [
        Ingredient(name="Broccoli", quantity="1", category="produce"),
        Ingredient(name="broccoli", quantity="2 cups", category="produce"),
    ]
    grocery = GroceryList.from_ingredients(ingredients, week_label="test")
    assert len(grocery.items) == 1
    assert "1" in grocery.items[0].quantity and "2 cups" in grocery.items[0].quantity
