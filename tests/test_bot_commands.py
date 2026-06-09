from datetime import date
from unittest.mock import MagicMock

from mealprepper.models.meals import Ingredient, MealRecipe, PlannedMeal, RecipeStep
from mealprepper.models.plans import PlanStatus, WeeklyPlan
from mealprepper.models.recipe_repository import SavedRecipe
from mealprepper.skills.comms.bot_commands import (
    MealPrepperBotHandler,
    parse_command_text,
    strip_bot_mention,
)
from mealprepper.skills.recipe_matching import recipe_match_score


def test_strip_bot_mention():
    assert strip_bot_mention("<@U123> approve") == "approve"


def test_parse_command_text_slash():
    assert parse_command_text("/mealprepper approve") == ("approve", "")
    assert parse_command_text("/mealprepper plan") == ("plan", "")
    assert parse_command_text("/mealprepper recipes chicken") == ("recipes", "chicken")
    assert parse_command_text("confirm plan-week") == ("confirm", "plan-week")


def test_parse_command_text_feedback():
    assert parse_command_text("loved chicken tacos") == ("loved", "chicken tacos")


def test_help_command():
    handler = MealPrepperBotHandler(supervisor=MagicMock(), recipe_repo=MagicMock())
    reply = handler.handle("help")
    assert reply.success
    assert "approve" in reply.text.lower()
    assert "plan-recipes" in reply.text.lower()
    assert "plan-week" in reply.text.lower()


def test_approve_without_pending():
    supervisor = MagicMock()
    supervisor.store.get_pending_approval.return_value = None
    handler = MealPrepperBotHandler(supervisor=supervisor, recipe_repo=MagicMock())
    reply = handler.handle("approve")
    assert not reply.success
    assert "waiting" in reply.text.lower()


def test_status_with_plan():
    supervisor = MagicMock()
    supervisor.store.get_pending_approval.return_value = None
    plan = WeeklyPlan(
        week_start=date(2026, 6, 2),
        week_end=date(2026, 6, 8),
        status=PlanStatus.APPROVED,
        meals=[],
    )
    supervisor.store.get_plan_for_date.return_value = plan

    def latest(status=None):
        if status == PlanStatus.PENDING_APPROVAL:
            return None
        return plan

    supervisor.store.get_latest_plan.side_effect = latest
    handler = MealPrepperBotHandler(supervisor=supervisor, recipe_repo=MagicMock())
    reply = handler.handle("status")
    assert reply.success
    assert reply.blocks
    assert "2026-06-02" in str(reply.blocks)


def test_daily_returns_meals_not_send_meta():
    supervisor = MagicMock()
    plan = WeeklyPlan(
        week_start=date(2026, 6, 9),
        week_end=date(2026, 6, 15),
        status=PlanStatus.APPROVED,
        meals=[
            PlannedMeal(
                day="tuesday",
                meal_block="adult_dinner",
                recipe=MealRecipe(title="Tacos", prep_minutes=10, cook_minutes=20),
            )
        ],
    )
    supervisor.store.get_plan_for_date.return_value = plan
    handler = MealPrepperBotHandler(supervisor=supervisor, recipe_repo=MagicMock())
    reply = handler.handle("daily")
    assert reply.success
    assert reply.payloads
    assert "Tacos" in str(reply.payloads)


def test_daily_explains_missing_current_week_plan():
    supervisor = MagicMock()
    supervisor.store.get_plan_for_date.return_value = None
    supervisor.store.get_latest_plan.return_value = WeeklyPlan(
        week_start=date(2026, 6, 1),
        week_end=date(2026, 6, 7),
        status=PlanStatus.APPROVED,
        meals=[],
    )
    handler = MealPrepperBotHandler(supervisor=supervisor, recipe_repo=MagicMock())
    reply = handler.handle("daily")
    assert not reply.success
    assert "2026-06-01" in str(reply.blocks)


def test_plan_uses_structured_blocks():
    supervisor = MagicMock()
    plan = WeeklyPlan(
        week_start=date(2026, 6, 1),
        week_end=date(2026, 6, 7),
        status=PlanStatus.APPROVED,
        meals=[
            PlannedMeal(
                day="monday",
                meal_block="adult_dinner",
                recipe=MealRecipe(title="Pasta"),
            )
        ],
    )
    supervisor.store.get_plan_for_date.return_value = None
    supervisor.store.get_latest_plan.return_value = plan
    handler = MealPrepperBotHandler(supervisor=supervisor, recipe_repo=MagicMock())
    reply = handler.handle("plan")
    assert reply.success
    assert reply.payloads
    assert "header" in str(reply.payloads[0]["blocks"])


def test_plan_recipes_includes_steps():
    supervisor = MagicMock()
    plan = WeeklyPlan(
        week_start=date(2026, 6, 1),
        week_end=date(2026, 6, 7),
        status=PlanStatus.APPROVED,
        meals=[
            PlannedMeal(
                day="monday",
                meal_block="adult_dinner",
                recipe=MealRecipe(
                    title="Pasta",
                    steps=[RecipeStep(order=1, instruction="Boil water")],
                ),
            )
        ],
    )
    supervisor.store.get_plan_for_date.return_value = None
    supervisor.store.get_latest_plan.return_value = plan
    handler = MealPrepperBotHandler(supervisor=supervisor, recipe_repo=MagicMock())
    reply = handler.handle("plan-recipes")
    assert reply.success
    assert reply.payloads
    assert "Boil water" in str(reply.payloads)


def test_plan_week_requires_confirmation():
    supervisor = MagicMock()
    supervisor.store.get_latest_plan.return_value = None
    handler = MealPrepperBotHandler(supervisor=supervisor, recipe_repo=MagicMock())
    reply = handler.handle("plan-week", channel="C123")
    assert reply.success
    assert "confirm plan-week" in str(reply.blocks).lower()
    assert handler._pending["C123"][0] == "plan-week"


def test_confirm_plan_week_defers_execution():
    supervisor = MagicMock()
    handler = MealPrepperBotHandler(supervisor=supervisor, recipe_repo=MagicMock())
    handler._pending["C123"] = ("plan-week", 9999999999)
    reply = handler.handle("confirm plan-week", channel="C123")
    assert reply.defer == "plan-week"
    assert "C123" not in handler._pending


def test_recipe_match_score_prefers_planned_title():
    assert recipe_match_score("hummus veggie pinwheels", "Hummus Veggie Pinwheels") == 1000
    assert recipe_match_score("hummus veggie pinwheels", "Quesadilla Sheet Bake") == 0


def test_recipe_prefers_planned_meal_over_saved_library():
    supervisor = MagicMock()
    plan = WeeklyPlan(
        week_start=date(2026, 6, 9),
        week_end=date(2026, 6, 15),
        status=PlanStatus.APPROVED,
        meals=[
            PlannedMeal(
                day="tuesday",
                meal_block="toddler_school_lunch",
                recipe=MealRecipe(
                    title="Hummus Veggie Pinwheels",
                    steps=[RecipeStep(order=1, instruction="Roll pinwheels")],
                ),
            )
        ],
    )
    supervisor.store.get_plan_for_date.return_value = plan
    saved = SavedRecipe(
        id="wrong",
        title="Quesadilla Sheet Bake",
        recipe=MealRecipe(
            title="Quesadilla Sheet Bake",
            steps=[RecipeStep(order=1, instruction="Bake quesadillas")],
        ),
    )
    supervisor.store.get_saved_recipe.return_value = saved
    recipe_repo = MagicMock()
    recipe_repo.search.return_value = [MagicMock(recipe_id="wrong", title="Quesadilla Sheet Bake")]
    handler = MealPrepperBotHandler(supervisor=supervisor, recipe_repo=recipe_repo)
    reply = handler.handle("recipe hummus veggie pinwheels")
    assert reply.success
    assert "Roll pinwheels" in str(reply.blocks)
    assert "Bake quesadillas" not in str(reply.blocks)


def test_grocery_defers_slow_generation():
    supervisor = MagicMock()
    plan = WeeklyPlan(
        week_start=date(2026, 6, 9),
        week_end=date(2026, 6, 15),
        status=PlanStatus.APPROVED,
        meals=[],
    )
    supervisor.store.get_latest_plan.return_value = plan
    handler = MealPrepperBotHandler(supervisor=supervisor, recipe_repo=MagicMock())
    reply = handler.handle("grocery")
    assert reply.defer == "grocery"
    supervisor.generate_grocery.assert_not_called()


def test_recipe_prefers_saved_library_over_weak_plan_match():
    supervisor = MagicMock()
    plan = WeeklyPlan(
        week_start=date(2026, 6, 9),
        week_end=date(2026, 6, 15),
        status=PlanStatus.APPROVED,
        meals=[
            PlannedMeal(
                day="monday",
                meal_block="adult_dinner",
                recipe=MealRecipe(
                    title="Sheet Pan Lemon Herb Chicken",
                    steps=[RecipeStep(order=1, instruction="Bake chicken")],
                ),
            )
        ],
    )
    supervisor.store.get_plan_for_date.return_value = plan
    saved = SavedRecipe(
        id="salad",
        title="Chicken Salad",
        recipe=MealRecipe(
            title="Chicken Salad",
            steps=[RecipeStep(order=1, instruction="Toss chicken with dressing")],
        ),
    )
    supervisor.store.get_saved_recipe.return_value = saved
    recipe_repo = MagicMock()
    recipe_repo.search.return_value = [MagicMock(recipe_id="salad", title="Chicken Salad")]
    handler = MealPrepperBotHandler(supervisor=supervisor, recipe_repo=recipe_repo)
    reply = handler.handle("recipe chicken salad")
    assert reply.success
    assert "Toss chicken with dressing" in str(reply.blocks)
    assert "Bake chicken" not in str(reply.blocks)


def test_recipe_resolves_planned_title_from_saved_library():
    supervisor = MagicMock()
    plan = WeeklyPlan(
        week_start=date(2026, 6, 9),
        week_end=date(2026, 6, 15),
        status=PlanStatus.APPROVED,
        meals=[
            PlannedMeal(
                day="monday",
                meal_block="adult_dinner",
                recipe=MealRecipe(
                    title="Sheet Pan Lemon Herb Chicken",
                    steps=[RecipeStep(order=1, instruction="Bake quesadillas")],
                ),
            )
        ],
    )
    supervisor.store.get_plan_for_date.return_value = plan
    saved = SavedRecipe(
        id="chicken",
        title="Sheet Pan Lemon Herb Chicken",
        recipe=MealRecipe(
            title="Sheet Pan Lemon Herb Chicken",
            steps=[RecipeStep(order=1, instruction="Roast lemon chicken")],
        ),
    )
    recipe_repo = MagicMock()
    recipe_repo.search.return_value = []
    recipe_repo.find_recipes_by_query.return_value = [saved]
    handler = MealPrepperBotHandler(supervisor=supervisor, recipe_repo=recipe_repo)
    reply = handler.handle("recipe sheet pan lemon herb chicken")
    assert reply.success
    assert "Roast lemon chicken" in str(reply.blocks)
    assert "Bake quesadillas" not in str(reply.blocks)


def test_recipe_shows_saved_steps():
    supervisor = MagicMock()
    supervisor.store.get_plan_for_date.return_value = None
    supervisor.store.get_latest_plan.return_value = None
    saved = SavedRecipe(
        id="abc",
        title="Smash Burger",
        recipe=MealRecipe(
            title="Smash Burger",
            ingredients=[Ingredient(name="beef", quantity="1", unit="lb")],
            steps=[RecipeStep(order=1, instruction="Smash on griddle")],
        ),
    )
    supervisor.store.get_saved_recipe.return_value = saved
    recipe_repo = MagicMock()
    recipe_repo.search.return_value = [MagicMock(recipe_id="abc", title="Smash Burger")]
    handler = MealPrepperBotHandler(supervisor=supervisor, recipe_repo=recipe_repo)
    reply = handler.handle("recipe smash burger")
    assert reply.success
    assert reply.blocks
    assert "Smash on griddle" in str(reply.blocks)
