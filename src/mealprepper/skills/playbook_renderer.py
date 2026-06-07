from __future__ import annotations

from datetime import date

from mealprepper.models.meals import PlannedMeal
from mealprepper.models.plans import WeeklyPlan
from mealprepper.skills.meal_finder import DAYS


class PlaybookRendererSkill:
    """Render weekly meal playbooks as markdown for approval and daily reference."""

    def render_plan(self, plan: WeeklyPlan) -> str:
        if plan.playbook_markdown:
            return plan.playbook_markdown
        return self.render_meals(plan.week_start, plan.week_end, plan.meals)

    def render_meals(
        self,
        week_start: date,
        week_end: date,
        meals: list[PlannedMeal],
    ) -> str:
        lines = [
            "# Weekly Meal Playbook",
            f"**{week_start.isoformat()} — {week_end.isoformat()}**",
            "",
        ]
        for day in DAYS:
            day_meals = [m for m in meals if m.day == day]
            if not day_meals:
                continue
            lines.append(f"## {day.title()}")
            for meal in day_meals:
                lines.extend(self._meal_section(meal))
        return "\n".join(lines)

    def render_titles_only(self, plan: WeeklyPlan) -> str:
        """Compact week view with meal block labels and recipe titles only."""
        lines = [
            f"# Week {plan.week_start} — {plan.week_end}",
            "",
        ]
        for day in DAYS:
            day_meals = [m for m in plan.meals if m.day == day]
            if not day_meals:
                continue
            lines.append(f"## {day.title()}")
            for meal in day_meals:
                block = meal.meal_block.replace("_", " ").title()
                line = f"- **{block}:** {meal.recipe.title}"
                if meal.cook_note:
                    line += f" _({meal.cook_note})_"
                lines.append(line)
            lines.append("")
        return "\n".join(lines).strip() + "\n"

    def render_approval_summary(self, plan: WeeklyPlan, max_meals: int = 12) -> str:
        lines = [
            f"Week {plan.week_start} — {plan.week_end}",
            f"{len(plan.meals)} meals planned.",
            "",
        ]
        for meal in plan.meals[:max_meals]:
            lines.append(
                f"• {meal.day[:3].title()} {meal.meal_block.replace('_', ' ')}: {meal.recipe.title}"
            )
        if len(plan.meals) > max_meals:
            lines.append(f"… and {len(plan.meals) - max_meals} more")
        if plan.synergy_notes:
            lines.append(f"\nSynergy: {plan.synergy_notes[:200]}")
        return "\n".join(lines)

    def _meal_section(self, meal: PlannedMeal) -> list[str]:
        r = meal.recipe
        lines = [
            f"### {meal.meal_block.replace('_', ' ').title()}: {r.title}",
            f"- Prep: {r.prep_minutes}m | Cook: {r.cook_minutes}m",
        ]
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
        return lines
