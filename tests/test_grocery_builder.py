from datetime import date

from mealprepper.models.grocery import GroceryCategory
from mealprepper.models.meals import Ingredient, MealRecipe, PlannedMeal
from mealprepper.models.plans import WeeklyPlan
from mealprepper.skills.grocery_builder import GroceryBuilderSkill
from mealprepper.skills.grocery_normalizer import (
    GroceryNormalizer,
    canonicalize_name,
    normalize_grocery_category,
    repair_ingredient,
)
from mealprepper.skills.pantry_config import PantryConfig


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
                        Ingredient(name="chicken thighs", quantity="1", unit="portion", category="meat"),
                        Ingredient(name="broccoli", quantity="1", unit="portion", category="produce"),
                        Ingredient(name="salt", quantity="to taste", unit="", category="spices"),
                    ],
                ),
            ),
            PlannedMeal(
                meal_block="adult_lunch",
                day="tuesday",
                recipe=MealRecipe(
                    title="Grain Bowl",
                    ingredients=[
                        Ingredient(name="broccoli", quantity="1", unit="portion", category="produce"),
                        Ingredient(name="quinoa", quantity="1", unit="cup", category="pantry"),
                        Ingredient(name="Greek yogurt", quantity="1", unit="portion", category="dairy"),
                        Ingredient(name="yogurt", quantity="1", unit="portion", category="dairy"),
                    ],
                ),
            ),
        ],
    )


def test_build_grocery_list_fallback_without_ollama():
    builder = GroceryBuilderSkill()
    grocery = builder.build(_plan_with_meals())
    assert grocery.weekly_plan_id == "test-plan"
    assert len(grocery.items) >= 2
    assert grocery.ready_for_shopping
    assert "salt" in [name.lower() for name in grocery.pantry_assumed]


def test_render_text_groups_sections():
    builder = GroceryBuilderSkill()
    normalizer = GroceryNormalizer()
    grocery = normalizer.build_shopping_list(
        [
            Ingredient(name="salmon", quantity="1", unit="portion", category="meat"),
            Ingredient(name="milk", quantity="1", unit="portion", category="dairy"),
            Ingredient(name="cinnamon", quantity="1", unit="tsp", category="spices"),
        ],
        "2025-06-02 — 2025-06-08",
    )
    text = builder.render_text(grocery)
    assert "Shop for recipes" in text
    assert "Weekly staples" in text
    assert "Already in pantry" in text
    assert "Salmon" in text or "salmon" in text.lower()


def test_canonicalize_merges_yogurt_variants():
    assert canonicalize_name("Greek yogurt") == canonicalize_name("yogurt")


def test_normalize_grocery_category_aliases():
    assert normalize_grocery_category("vegetable") == GroceryCategory.PRODUCE
    assert normalize_grocery_category("grain") == GroceryCategory.PANTRY


def test_normalizer_uses_shoppable_quantities():
    normalizer = GroceryNormalizer()
    grocery = normalizer.build_shopping_list(
        [Ingredient(name="eggs", quantity="1", unit="portion", category="dairy")],
        "test-week",
    )
    eggs = next(i for i in grocery.weekly_staples if "egg" in i.name.lower())
    assert "dozen" in eggs.quantity.lower()


def test_repair_ingredient_splits_quantity_from_name():
    fixed = repair_ingredient(Ingredient(name="1 1/2 C Shredded Mozzarella", quantity="1"))
    assert fixed is not None
    assert "mozzarella" in fixed.name.lower()
    assert fixed.quantity
    assert repair_ingredient(Ingredient(name="3", quantity="1")) is None
    assert repair_ingredient(Ingredient(name="—", quantity="1")) is None


def test_repair_ingredient_does_not_strip_leading_letters():
    for name in (
        "Can 20 Ounces Pineapple Chunks Drained",
        "Garlic Minced",
        "Carrot Sticks",
        "Cream Of Chicken Soup",
    ):
        fixed = repair_ingredient(Ingredient(name=name, quantity="1"))
        assert fixed is not None
        assert fixed.name == name, f"mutated name {name!r} -> {fixed.name!r}"


def test_pantry_config_matches_spices():
    pantry = PantryConfig(on_hand={"cinnamon", "salt"}, weekly_staples={"milk"})
    assert pantry.matches_on_hand("ground cinnamon")
    assert pantry.matches_weekly_staple("whole milk")
