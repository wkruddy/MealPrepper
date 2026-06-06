from datetime import date, timedelta

from mealprepper.models.family import FamilyProfile, MemberRole
from mealprepper.models.feedback import PreferenceProfile
from mealprepper.models.meals import Ingredient, MealRecipe, PlannedMeal, RecipeStep
from mealprepper.models.plans import WeeklyPlan, PlanStatus
from mealprepper.models.grocery import GroceryList
from mealprepper.skills.ingredient_synergy import IngredientSynergySkill
from mealprepper.skills.grocery_builder import GroceryBuilderSkill


def test_family_from_config():
    config = {
        "members": [{"id": "t1", "name": "Kid", "role": "toddler", "constraints": {}}],
        "meal_blocks": ["adult_dinner"],
    }
    family = FamilyProfile.from_config(config)
    assert len(family.members) == 1
    assert family.members[0].role == MemberRole.TODDLER


def test_weekly_plan_ingredients():
    meal = PlannedMeal(
        meal_block="adult_dinner",
        day="monday",
        recipe=MealRecipe(
            title="Test",
            ingredients=[Ingredient(name="Chicken"), Ingredient(name="Rice")],
        ),
    )
    start = date.today()
    plan = WeeklyPlan(week_start=start, week_end=start + timedelta(days=6), meals=[meal])
    assert "chicken" in plan.ingredient_names()


def test_grocery_dedup():
    items = [
        Ingredient(name="Eggs", quantity="6"),
        Ingredient(name="eggs", quantity="12"),
    ]
    grocery = GroceryList.from_ingredients(items, week_label="test")
    assert len(grocery.items) == 1


def test_synergy_fallback():
    meal = PlannedMeal(
        meal_block="adult_dinner",
        day="monday",
        recipe=MealRecipe(
            title="A",
            ingredients=[Ingredient(name="Broccoli"), Ingredient(name="UniqueItem")],
        ),
    )
    meal2 = PlannedMeal(
        meal_block="adult_dinner",
        day="tuesday",
        recipe=MealRecipe(
            title="B",
            ingredients=[Ingredient(name="Broccoli"), Ingredient(name="OtherUnique")],
        ),
    )
    start = date.today()
    plan = WeeklyPlan(week_start=start, week_end=start + timedelta(days=6), meals=[meal, meal2])
    report = IngredientSynergySkill().analyze(plan)
    assert "broccoli" in report.shared_ingredients


def test_preference_context():
    profile = PreferenceProfile(liked_meals=["Pasta"], disliked_meals=["Liver"])
    ctx = profile.to_prompt_context()
    assert "Pasta" in ctx
    assert "Liver" in ctx
