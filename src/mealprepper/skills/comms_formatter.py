from __future__ import annotations

from datetime import date

from mealprepper.models.plans import DailyPlanSummary, WeeklyPlan


class CommsFormatterSkill:
    """Format SMS-friendly summaries for approvals and daily reminders."""

    def format_approval(self, plan: WeeklyPlan) -> str:
        lines = [
            f"Week {plan.week_start} — {plan.week_end}",
            f"{len(plan.meals)} meals. Reply APPROVE or list changes.",
            "",
        ]
        dinners = [m for m in plan.meals if m.meal_block == "adult_dinner"]
        for meal in dinners[:5]:
            lines.append(f"• {meal.day[:3].title()} dinner: {meal.recipe.title}")
        return "\n".join(lines)

    def format_daily_summary(self, summary: DailyPlanSummary) -> str:
        lines = [
            f"{summary.day_name.title()} ({summary.plan_date.isoformat()})",
            "",
        ]
        for meal in summary.meals:
            block = meal.meal_block.replace("_", " ").title()
            prep = meal.recipe.prep_minutes + meal.recipe.cook_minutes
            lines.append(f"• {block}: {meal.recipe.title} (~{prep}m)")
            if meal.meal_block == "infant_blw" and meal.recipe.infant_guidance:
                lines.append(f"  BLW: {meal.recipe.infant_guidance[:120]}")
        if summary.prep_notes:
            lines.append(f"\nPrep: {summary.prep_notes}")
        if summary.infant_blw_tips:
            lines.append(f"Infant tips: {summary.infant_blw_tips}")
        return "\n".join(lines)

    def format_grocery_ready(self, item_count: int, week_label: str) -> str:
        return f"Grocery list ready for {week_label}: {item_count} items. Happy shopping!"

    def format_feedback_ack(self, meal_title: str, rating: str) -> str:
        return f"Thanks! Recorded '{rating}' for {meal_title}."

    def daily_summary_from_plan(self, plan: WeeklyPlan, target: date) -> DailyPlanSummary:
        day_name = target.strftime("%A").lower()
        meals = plan.meals_for_day(day_name)
        blw = [m for m in meals if m.meal_block == "infant_blw"]
        infant_tips = blw[0].recipe.infant_guidance if blw else ""
        prep_notes = ""
        bulk = [m for m in meals if m.meal_block == "bulk_meal_prep"]
        if bulk:
            prep_notes = f"Bulk prep: {bulk[0].recipe.title}"
        return DailyPlanSummary(
            plan_date=target,
            day_name=day_name,
            meals=meals,
            prep_notes=prep_notes,
            infant_blw_tips=infant_tips,
        )
