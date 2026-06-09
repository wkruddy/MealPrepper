from datetime import date

from mealprepper.models.meals import Ingredient, MealRecipe, PlannedMeal, RecipeStep
from mealprepper.models.plans import WeeklyPlan
from mealprepper.skills.food_shelf_life import FoodShelfLifeSkill
from mealprepper.skills.meal_blocks import WeekMealOutline
from mealprepper.skills.meal_catalog import MealCatalog
from mealprepper.skills.playbook_renderer import PlaybookRendererSkill


def _outline(day: str, block: str, title: str, **kwargs) -> WeekMealOutline:
    return WeekMealOutline(day=day, meal_block=block, title=title, key_ingredients=["salmon"], **kwargs)


def test_classify_seafood():
    skill = FoodShelfLifeSkill()
    category = skill.classify_meal("Baked Salmon & Roasted Veg", ["salmon"])
    assert category.name == "seafood"
    assert category.fridge_days == 2


def test_reuse_is_invalid_for_late_seafood():
    skill = FoodShelfLifeSkill()
    assert skill.reuse_is_valid("Baked Salmon", "monday", "tuesday", ["salmon"])
    assert not skill.reuse_is_valid("Baked Salmon", "monday", "friday", ["salmon"])


def test_validate_outlines_replaces_unsafe_reuse():
    skill = FoodShelfLifeSkill()
    catalog = MealCatalog()
    outlines = [
        _outline("monday", "adult_dinner", "Baked Salmon"),
        _outline(
            "friday",
            "adult_dinner",
            "Baked Salmon",
            reuse_of_day="monday",
            reuse_of_block="adult_dinner",
            cook_note="Same as Monday dinner",
        ),
    ]
    result = skill.validate_outlines(outlines, catalog)
    friday = next(item for item in result if item.day == "friday")
    assert friday.title != "Baked Salmon"
    assert friday.reuse_of_day is None


def test_audit_plan_flags_stale_leftovers():
    plan = WeeklyPlan(
        week_start=date(2025, 6, 2),
        week_end=date(2025, 6, 8),
        meals=[
            PlannedMeal(
                meal_block="adult_dinner",
                day="monday",
                recipe=MealRecipe(title="Baked Salmon", ingredients=[Ingredient(name="salmon")]),
            ),
            PlannedMeal(
                meal_block="adult_dinner",
                day="friday",
                recipe=MealRecipe(title="Baked Salmon", ingredients=[Ingredient(name="salmon")]),
                cook_source_day="monday",
                cook_source_block="adult_dinner",
            ),
        ],
    )
    audit = FoodShelfLifeSkill().audit_plan(plan)
    assert not audit.ok
    assert any("salmon" in warning.lower() for warning in audit.warnings)


def test_render_full_recipes_includes_steps():
    plan = WeeklyPlan(
        week_start=date(2025, 6, 2),
        week_end=date(2025, 6, 8),
        meals=[
            PlannedMeal(
                meal_block="adult_dinner",
                day="monday",
                recipe=MealRecipe(
                    title="Test Dinner",
                    ingredients=[Ingredient(name="rice", quantity="1", unit="cup")],
                    steps=[RecipeStep(order=1, instruction="Cook rice.", duration_minutes=20)],
                    toddler_modifications="No spice.",
                ),
            )
        ],
    )
    text = PlaybookRendererSkill().render_full_recipes(plan)
    assert "**Ingredients**" in text
    assert "**Steps**" in text
    assert "Cook rice." in text
    assert "**Toddler:**" in text
