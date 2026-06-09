from datetime import date

from mealprepper.models.meals import Ingredient, MealRecipe, PlannedMeal, RecipeStep
from mealprepper.models.plans import PlanStatus, WeeklyPlan
from mealprepper.skills.comms.slack_format import (
    SlackMessageBuilder,
    build_week_recipes_messages,
    build_week_titles_messages,
    markdown_to_slack_mrkdwn,
    slack_message_payload,
)


def test_markdown_headers_and_bold_converted():
    text = "# Week 2026-06-01\n## Monday\n- **Adult Dinner:** Tacos"
    converted = markdown_to_slack_mrkdwn(text)
    assert "*Week 2026-06-01*" in converted
    assert "*Monday*" in converted
    assert "*Adult Dinner:*" in converted


def test_slack_message_builder_uses_headers_and_dividers():
    builder = SlackMessageBuilder()
    builder.header("Week plan").divider().section("• *Dinner:* Pasta")
    payload = builder.to_payload()
    types = [block["type"] for block in payload["blocks"]]
    assert types == ["header", "divider", "section"]


def test_build_week_titles_messages_groups_by_day():
    plan = WeeklyPlan(
        week_start=date(2026, 6, 1),
        week_end=date(2026, 6, 7),
        status=PlanStatus.APPROVED,
        meals=[
            PlannedMeal(
                day="monday",
                meal_block="adult_dinner",
                recipe=MealRecipe(title="Tacos"),
            )
        ],
    )
    payloads = build_week_titles_messages(plan)
    block_types = [block["type"] for block in payloads[0]["blocks"]]
    assert "header" in block_types
    assert "divider" in block_types


def test_build_week_recipes_messages_include_steps():
    plan = WeeklyPlan(
        week_start=date(2026, 6, 1),
        week_end=date(2026, 6, 7),
        status=PlanStatus.APPROVED,
        meals=[
            PlannedMeal(
                day="monday",
                meal_block="adult_dinner",
                recipe=MealRecipe(
                    title="Tacos",
                    ingredients=[Ingredient(name="tortillas", quantity="8", unit="count")],
                    steps=[RecipeStep(order=1, instruction="Warm tortillas")],
                ),
            )
        ],
    )
    payloads = build_week_recipes_messages(plan)
    body = str(payloads[0]["blocks"])
    assert "Warm tortillas" in body
    assert "Ingredients" in body


def test_slack_message_payload_uses_blocks():
    payload = slack_message_payload("## Monday\n- **Dinner:** Pasta")
    assert payload["blocks"]
    assert payload["blocks"][0]["text"]["type"] == "mrkdwn"
