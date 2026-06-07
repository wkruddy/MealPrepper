from datetime import date

from mealprepper.models.meals import Ingredient, MealRecipe, PlannedMeal
from mealprepper.models.plans import WeeklyPlan
from mealprepper.skills.ingredient_synergy import IngredientSynergySkill, SynergyReport


def _meal(day: str, title: str, ingredients: list[str]) -> PlannedMeal:
    return PlannedMeal(
        meal_block="adult_dinner",
        day=day,
        recipe=MealRecipe(
            title=title,
            ingredients=[Ingredient(name=n, quantity="1", category="produce") for n in ingredients],
        ),
    )


def test_analyze_finds_shared_ingredients_without_llm():
    skill = IngredientSynergySkill()
    plan = WeeklyPlan(
        week_start=date(2025, 6, 2),
        week_end=date(2025, 6, 8),
        meals=[
            _meal("monday", "Chicken Broccoli", ["chicken", "broccoli", "rice"]),
            _meal("tuesday", "Beef Broccoli", ["beef", "broccoli", "garlic"]),
        ],
    )
    report = skill.analyze(plan)
    assert "broccoli" in report.shared_ingredients
    assert report.notes or report.suggestions


def test_consolidate_ingredients_merges_duplicates():
    skill = IngredientSynergySkill()
    meals = [
        _meal("monday", "A", ["broccoli", "rice"]),
        _meal("tuesday", "B", ["broccoli", "garlic"]),
    ]
    merged = skill.consolidate_ingredients(meals)
    names = {i.name for i in merged}
    assert names == {"broccoli", "rice", "garlic"}
    broccoli = next(i for i in merged if i.name == "broccoli")
    assert "1" in broccoli.quantity


def test_apply_synergy_notes_updates_plan():
    skill = IngredientSynergySkill()
    plan = WeeklyPlan(
        week_start=date(2025, 6, 2),
        week_end=date(2025, 6, 8),
        meals=[_meal("monday", "Test", ["spinach"])],
    )
    report = skill.analyze(plan)
    updated = skill.apply_synergy_notes(plan, report)
    assert updated.synergy_notes
    assert "Ingredient Synergy" in updated.playbook_markdown


def test_synergy_report_normalizes_structured_llm_output():
    report = SynergyReport.model_validate(
        {
            "suggestions": [
                {
                    "type": "ingredient_swap",
                    "description": "Use leftover turkey in wraps to reduce waste.",
                },
                {"type": "combine_meals", "text": "Serve salmon over beef and broccoli."},
            ],
            "notes": [
                "Toddler meals reuse cucumber and oregano to reduce waste.",
                "Batch cook grains on Saturday.",
            ],
        }
    )
    assert len(report.suggestions) == 2
    assert "turkey" in report.suggestions[0].lower()
    assert "salmon" in report.suggestions[1].lower()
    assert "Toddler meals" in report.notes
    assert "Batch cook" in report.notes
