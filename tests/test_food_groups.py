from mealprepper.models.meals import Ingredient, MealRecipe
from mealprepper.skills.food_groups import FoodGroupsSkill


def test_classify_ingredient_matches_multiple_groups():
    skill = FoodGroupsSkill()
    groups = skill.classify_ingredient("cheddar cheese")
    assert "protein" in groups


def test_analyze_detects_covered_groups():
    skill = FoodGroupsSkill()
    coverage = skill.analyze(
        "Ham and Cheese Sliders",
        ["Hawaiian sweet rolls", "deli ham", "Swiss cheese", "butter"],
    )
    assert coverage.covered["carb"]
    assert coverage.covered["protein"]
    assert coverage.covered["fat"]
    assert "veggie" in coverage.missing
    assert "fruit" in coverage.missing


def test_annotate_toddler_meal_suggests_missing_groups():
    skill = FoodGroupsSkill()
    recipe = MealRecipe(
        title="Turkey Roll-ups",
        ingredients=[
            Ingredient(name="turkey"),
            Ingredient(name="tortilla"),
            Ingredient(name="cheese"),
        ],
        toddler_modifications="No spice.",
    )
    updated = skill.annotate_recipe(recipe, "toddler_school_lunch")
    assert updated.food_groups["protein"]
    assert updated.food_groups["carb"]
    assert "veggie" in updated.food_groups
    assert "Add:" in updated.toddler_modifications
    assert "Food groups" in updated.toddler_modifications


def test_annotate_adult_meal_notes_without_enforcement():
    skill = FoodGroupsSkill()
    recipe = MealRecipe(
        title="Grilled Chicken Salad",
        ingredients=[
            Ingredient(name="chicken"),
            Ingredient(name="mixed greens"),
            Ingredient(name="olive oil"),
        ],
    )
    updated = skill.annotate_recipe(recipe, "adult_dinner")
    assert updated.food_groups["protein"]
    assert updated.food_groups["veggie"]
    assert updated.food_groups["fat"]
    assert "Add:" not in (updated.toddler_modifications or "")


def test_skips_infant_blw():
    skill = FoodGroupsSkill()
    recipe = MealRecipe(title="Soft Banana Strips", ingredients=[Ingredient(name="banana")])
    updated = skill.annotate_recipe(recipe, "infant_blw")
    assert updated.food_groups == {}
    assert updated.toddler_modifications == ""


def test_strict_for_block():
    skill = FoodGroupsSkill()
    assert skill.strict_for_block("toddler_breakfast")
    assert not skill.strict_for_block("adult_dinner")
    assert not skill.note_for_block("infant_blw")
