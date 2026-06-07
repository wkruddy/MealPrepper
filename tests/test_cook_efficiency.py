from datetime import date, timedelta

from mealprepper.models.meals import Ingredient, MealRecipe, PlannedMeal
from mealprepper.models.plans import WeeklyPlan
from mealprepper.skills.cook_efficiency import CookEfficiencyConfig, CookEfficiencySkill
from mealprepper.skills.meal_blocks import WeekMealOutline
from mealprepper.skills.week_outline import finalize_outline
from mealprepper.skills.meal_catalog import MealCatalog


def _outline(day: str, block: str, title: str) -> WeekMealOutline:
    return WeekMealOutline(day=day, meal_block=block, title=title, key_ingredients=["chicken"])


def test_apply_links_dinner_to_next_lunch():
    skill = CookEfficiencySkill(CookEfficiencyConfig(repeat_dinners=False))
    outlines = [
        _outline("monday", "adult_dinner", "Sheet Pan Chicken"),
        _outline("tuesday", "adult_lunch", "Grain Bowl"),
    ]
    result = skill.apply_to_outlines(outlines)
    tuesday_lunch = next(o for o in result if o.day == "tuesday" and o.meal_block == "adult_lunch")
    assert tuesday_lunch.title == "Sheet Pan Chicken"
    assert tuesday_lunch.reuse_of_day == "monday"
    assert tuesday_lunch.reuse_of_block == "adult_dinner"
    assert "Leftovers" in tuesday_lunch.cook_note


def test_apply_repeats_dinners_to_limit_sessions():
    skill = CookEfficiencySkill(
        CookEfficiencyConfig(
            cross_block_reuse=False,
            max_dinner_cook_sessions=2,
        )
    )
    outlines = [
        _outline("monday", "adult_dinner", "Stir Fry A"),
        _outline("tuesday", "adult_dinner", "Stir Fry B"),
        _outline("wednesday", "adult_dinner", "Stir Fry C"),
        _outline("thursday", "adult_dinner", "Stir Fry D"),
    ]
    result = skill.apply_to_outlines(outlines)
    titles = [o.title for o in result if o.meal_block == "adult_dinner"]
    assert len(set(titles)) == 2
    repeated = next(o for o in result if o.day == "thursday" and o.meal_block == "adult_dinner")
    assert repeated.reuse_of_day in {"monday", "tuesday"}


def test_build_report_counts_cook_sessions():
    plan = WeeklyPlan(
        week_start=date(2025, 6, 2),
        week_end=date(2025, 6, 8),
        meals=[
            PlannedMeal(
                meal_block="adult_dinner",
                day="monday",
                recipe=MealRecipe(
                    title="Chicken",
                    ingredients=[Ingredient(name="chicken"), Ingredient(name="broccoli")],
                ),
            ),
            PlannedMeal(
                meal_block="adult_lunch",
                day="tuesday",
                recipe=MealRecipe(title="Chicken"),
                cook_source_day="monday",
                cook_source_block="adult_dinner",
                cook_note="Leftovers from Monday dinner",
            ),
            PlannedMeal(
                meal_block="adult_dinner",
                day="wednesday",
                recipe=MealRecipe(
                    title="Salmon",
                    ingredients=[Ingredient(name="salmon"), Ingredient(name="broccoli")],
                ),
            ),
        ],
        synergy_notes="Batch cook broccoli.",
        synergy_suggestions=["Use leftover chicken in wraps."],
    )
    report = CookEfficiencySkill().build_report(plan)
    assert len(report.reuse_links) == 1
    assert report.reuse_links[0].link_type == "leftover"
    assert "broccoli" in report.shared_ingredients
    assert "Batch cook broccoli." in report.synergy_notes
    assert report.estimated_cook_sessions >= 2


def test_finalize_outline_applies_cook_efficiency():
    catalog = MealCatalog()
    outlines = [
        _outline(day, "adult_dinner", f"Dinner {index}")
        for index, day in enumerate(
            ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
        )
    ]
    cfg = CookEfficiencyConfig(enabled=True, max_dinner_cook_sessions=3, cross_block_reuse=True)
    result = finalize_outline(
        outlines,
        date(2025, 6, 2),
        catalog,
        max_repeat=3,
        cook_efficiency=cfg,
    )
    tuesday_lunch = next(
        (o for o in result if o.day == "tuesday" and o.meal_block == "adult_lunch"),
        None,
    )
    assert tuesday_lunch is not None
    assert tuesday_lunch.reuse_of_day == "monday"
