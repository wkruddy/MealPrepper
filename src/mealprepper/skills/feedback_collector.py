from __future__ import annotations

import re
from datetime import date

from mealprepper.models.feedback import FeedbackRating, MealFeedback
from mealprepper.models.plans import WeeklyPlan


class FeedbackCollectorSkill:
    """Parse inbound SMS-style feedback into structured MealFeedback records."""

    RATING_PATTERNS: dict[FeedbackRating, re.Pattern[str]] = {
        FeedbackRating.LOVED: re.compile(r"\bloved\b", re.I),
        FeedbackRating.LIKED: re.compile(r"\bliked\b", re.I),
        FeedbackRating.NEUTRAL: re.compile(r"\bneutral\b|\bok\b|\bfine\b", re.I),
        FeedbackRating.DISLIKED: re.compile(r"\bdisliked\b|\bhate\b|\bbad\b", re.I),
        FeedbackRating.REJECT: re.compile(r"\breject\b|\bnever\b", re.I),
    }

    def parse_message(
        self,
        text: str,
        *,
        meal_title: str = "",
        meal_block: str = "",
        day: str = "",
        member_id: str | None = None,
    ) -> MealFeedback | None:
        text = text.strip()
        if not text:
            return None

        rating = self._detect_rating(text)
        if not rating:
            return None

        title = meal_title or self._extract_meal_title(text)
        if not title:
            title = "Unknown meal"

        return MealFeedback(
            meal_title=title,
            meal_block=meal_block,
            day=day,
            rating=rating,
            comment=text,
            member_id=member_id,
        )

    def parse_approval(self, text: str) -> bool | None:
        text = text.strip().lower()
        if re.search(r"\bapprove\b|\byes\b|\blooks good\b|\bok\b", text):
            return True
        if re.search(r"\breject\b|\bno\b|\bchange\b", text):
            return False
        return None

    def suggest_meal_for_feedback(self, plan: WeeklyPlan | None, target: date | None = None) -> str:
        if not plan or not plan.meals:
            return "last night's dinner"
        target = target or date.today()
        day_name = target.strftime("%A").lower()
        day_meals = plan.meals_for_day(day_name)
        dinners = [m for m in day_meals if m.meal_block == "adult_dinner"]
        if dinners:
            return dinners[0].recipe.title
        if day_meals:
            return day_meals[-1].recipe.title
        return plan.meals[-1].recipe.title

    def _detect_rating(self, text: str) -> FeedbackRating | None:
        for rating, pattern in self.RATING_PATTERNS.items():
            if pattern.search(text):
                return rating
        return None

    def _extract_meal_title(self, text: str) -> str:
        quoted = re.search(r"['\"]([^'\"]+)['\"]", text)
        if quoted:
            return quoted.group(1)
        for_match = re.search(r"\bfor\s+(.+?)(?:\?|$)", text, re.I)
        if for_match:
            return for_match.group(1).strip()
        return ""
