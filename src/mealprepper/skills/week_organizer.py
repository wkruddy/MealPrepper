from __future__ import annotations

import logging
from datetime import date, timedelta

from mealprepper.models.family import FamilyProfile
from mealprepper.models.meals import PlannedMeal
from mealprepper.models.plans import WeeklyPlan, PlanStatus
from mealprepper.skills.meal_finder import DAYS, MealFinderSkill, WeekMealOutline

logger = logging.getLogger(__name__)


class WeekOrganizerSkill:
    """Organize meals into a structured weekly plan with playbook markdown."""

    def __init__(self, meal_finder: MealFinderSkill | None = None) -> None:
        self.meal_finder = meal_finder or MealFinderSkill()

    def week_bounds(self, reference: date | None = None) -> tuple[date, date]:
        ref = reference or date.today()
        start = ref - timedelta(days=ref.weekday())
        end = start + timedelta(days=6)
        return start, end

    def organize_week(
        self,
        family: FamilyProfile,
        preferences,
        week_start: date | None = None,
    ) -> WeeklyPlan:
        start, end = self.week_bounds(week_start)
        logger.info("Organizing week %s — %s", start, end)
        outlines = self.meal_finder.find_week_outline(family, preferences, start)
        meals = self.meal_finder.outline_to_planned_meals(outlines, family)
        logger.info("Week organized: %d planned meals", len(meals))
        playbook = self.render_playbook(start, end, meals)
        return WeeklyPlan(
            week_start=start,
            week_end=end,
            status=PlanStatus.DRAFT,
            meals=meals,
            playbook_markdown=playbook,
        )

    def render_playbook(self, week_start: date, week_end: date, meals: list[PlannedMeal]) -> str:
        lines = [
            f"# Weekly Meal Playbook",
            f"**{week_start.isoformat()} — {week_end.isoformat()}**",
            "",
        ]
        for day in DAYS:
            day_meals = [m for m in meals if m.day == day]
            if not day_meals:
                continue
            lines.append(f"## {day.title()}")
            for meal in day_meals:
                r = meal.recipe
                lines.append(f"### {meal.meal_block.replace('_', ' ').title()}: {r.title}")
                lines.append(f"- Prep: {r.prep_minutes}m | Cook: {r.cook_minutes}m")
                if r.ingredients:
                    ing = ", ".join(
                        f"{i.name} ({i.quantity} {i.unit})".strip() for i in r.ingredients[:8]
                    )
                    lines.append(f"- Ingredients: {ing}")
                if r.infant_guidance:
                    lines.append(f"- Infant BLW: {r.infant_guidance}")
                if r.toddler_modifications:
                    lines.append(f"- Toddler: {r.toddler_modifications}")
                lines.append("")
        return "\n".join(lines)
