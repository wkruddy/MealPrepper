from __future__ import annotations

import re
from typing import TYPE_CHECKING

from mealprepper.skills.meal_blocks import DAYS

if TYPE_CHECKING:
    from mealprepper.models.meals import PlannedMeal
    from mealprepper.models.plans import WeeklyPlan

MAX_BLOCKS = 50
MAX_SECTION_CHARS = 2900


def markdown_to_slack_mrkdwn(text: str) -> str:
    """Convert common markdown to Slack mrkdwn (*bold*, _italic_, bullets)."""
    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("### "):
            lines.append(f"*{stripped[4:].strip()}*")
            continue
        if stripped.startswith("## "):
            lines.append(f"*{stripped[3:].strip()}*")
            continue
        if stripped.startswith("# "):
            lines.append(f"*{stripped[2:].strip()}*")
            continue
        lines.append(line)

    converted = "\n".join(lines)
    converted = re.sub(r"\*\*(.+?)\*\*", r"*\1*", converted)
    converted = converted.replace("**", "*")
    return converted.strip()


def chunk_slack_text(text: str, max_len: int = MAX_SECTION_CHARS) -> list[str]:
    if len(text) <= max_len:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in text.splitlines():
        line_len = len(line) + 1
        if current and current_len + line_len > max_len:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += line_len
    if current:
        chunks.append("\n".join(current))
    return chunks


class SlackMessageBuilder:
    """Build Slack Block Kit messages with headers, dividers, and sections."""

    def __init__(self) -> None:
        self.blocks: list[dict] = []
        self._fallback_parts: list[str] = []

    def header(self, text: str) -> SlackMessageBuilder:
        self.blocks.append(
            {
                "type": "header",
                "text": {"type": "plain_text", "text": text[:150], "emoji": True},
            }
        )
        self._fallback_parts.append(text)
        return self

    def divider(self) -> SlackMessageBuilder:
        self.blocks.append({"type": "divider"})
        return self

    def section(self, mrkdwn: str) -> SlackMessageBuilder:
        converted = markdown_to_slack_mrkdwn(mrkdwn)
        for chunk in chunk_slack_text(converted):
            self.blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": chunk}})
        self._fallback_parts.append(re.sub(r"[*_~`]", "", converted)[:120])
        return self

    def context(self, mrkdwn: str) -> SlackMessageBuilder:
        self.blocks.append(
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": markdown_to_slack_mrkdwn(mrkdwn)[:3000]}],
            }
        )
        return self

    def fallback_text(self) -> str:
        return " · ".join(part for part in self._fallback_parts if part)[:300] or "MealPrepper"

    def to_payload(self) -> dict:
        return {"text": self.fallback_text(), "blocks": self.blocks[:MAX_BLOCKS]}

    def build_messages(self) -> list[dict]:
        """Split into multiple payloads if block count exceeds Slack limits."""
        if len(self.blocks) <= MAX_BLOCKS:
            return [self.to_payload()]

        messages: list[dict] = []
        for index in range(0, len(self.blocks), MAX_BLOCKS):
            chunk = self.blocks[index : index + MAX_BLOCKS]
            text = self.fallback_text() if index == 0 else "MealPrepper (continued)"
            messages.append({"text": text, "blocks": chunk})
        return messages


def slack_message_payload(text: str) -> dict:
    """Build a simple mrkdwn payload from plain text."""
    builder = SlackMessageBuilder()
    builder.section(text)
    return builder.to_payload()


def format_planned_meal_recipe(meal: PlannedMeal) -> str:
    recipe = meal.recipe
    block = meal.meal_block.replace("_", " ").title()
    lines = [f"*{block}: {recipe.title}*"]
    if meal.cook_note:
        lines.append(f"_{meal.cook_note}_")
    if recipe.description:
        lines.append(recipe.description)
    prep = recipe.prep_minutes + recipe.cook_minutes
    if prep:
        lines.append(f"_~{prep} min total_")
    if recipe.ingredients:
        lines.append("*Ingredients*")
        for ingredient in recipe.ingredients:
            qty = " ".join(part for part in [ingredient.quantity, ingredient.unit] if part).strip()
            name = ingredient.name.strip()
            if name:
                lines.append(f"• {name}" + (f" — {qty}" if qty else ""))
            elif qty:
                lines.append(f"• {qty}")
    if recipe.steps:
        lines.append("*Steps*")
        for step in sorted(recipe.steps, key=lambda item: item.order):
            suffix = f" _({step.duration_minutes}m)_" if step.duration_minutes else ""
            lines.append(f"{step.order}. {step.instruction}{suffix}")
    if recipe.toddler_modifications:
        lines.append(f"_Toddler:_ {recipe.toddler_modifications}")
    if recipe.infant_guidance:
        lines.append(f"_Infant BLW:_ {recipe.infant_guidance}")
    return "\n".join(lines)


def build_week_titles_messages(plan: WeeklyPlan, *, stale_note: str = "") -> list[dict]:
    builder = SlackMessageBuilder()
    builder.header(f"Week {plan.week_start} — {plan.week_end}")
    if stale_note:
        builder.context(stale_note)
    builder.divider()

    day_blocks: list[tuple[str, list[str]]] = []
    for day in DAYS:
        day_meals = [meal for meal in plan.meals if meal.day == day]
        if not day_meals:
            continue
        lines = []
        for meal in day_meals:
            block = meal.meal_block.replace("_", " ").title()
            line = f"• *{block}:* {meal.recipe.title}"
            if meal.cook_note:
                line += f" _({meal.cook_note})_"
            lines.append(line)
        day_blocks.append((day.title(), lines))

    for index, (day_name, lines) in enumerate(day_blocks):
        builder.header(day_name)
        builder.section("\n".join(lines))
        if index < len(day_blocks) - 1:
            builder.divider()

    return builder.build_messages()


def build_week_recipes_messages(plan: WeeklyPlan, *, stale_note: str = "") -> list[dict]:
    """One Slack message per day so full recipes stay readable."""
    messages: list[dict] = []
    for day_index, day in enumerate(DAYS):
        day_meals = [meal for meal in plan.meals if meal.day == day]
        if not day_meals:
            continue

        builder = SlackMessageBuilder()
        builder.header(f"{day.title()} recipes")
        if day_index == 0 and stale_note:
            builder.context(stale_note)
        builder.divider()
        for meal_index, meal in enumerate(day_meals):
            builder.section(format_planned_meal_recipe(meal))
            if meal_index < len(day_meals) - 1:
                builder.divider()
        messages.extend(builder.build_messages())
    return messages or [slack_message_payload("No meals found in this plan.")]


def build_daily_messages(day_name: str, plan_date: str, meal_lines: list[str], extras: list[str]) -> list[dict]:
    builder = SlackMessageBuilder()
    builder.header(f"{day_name} — {plan_date}")
    builder.divider()
    builder.section("\n".join(meal_lines))
    for extra in extras:
        if extra.strip():
            builder.divider()
            builder.section(extra)
    return builder.build_messages()
