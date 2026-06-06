from __future__ import annotations

import logging

from mealprepper.llm.ollama_client import OllamaClient, OllamaUnavailableError
from mealprepper.models.feedback import MealFeedback, PreferenceProfile
from mealprepper.models.plans import WeeklyPlan

logger = logging.getLogger(__name__)

MAX_NOTES_CHARS = 500
MAX_LIST_ITEMS = 20


class ContextCompressor:
    """Summarize long histories into compact preference/plan summaries for prompts."""

    def __init__(self, llm: OllamaClient | None = None) -> None:
        self.llm = llm or OllamaClient()

    def compress_profile(self, profile: PreferenceProfile) -> PreferenceProfile:
        """Return a compact copy suitable for LLM prompts."""
        return PreferenceProfile(
            liked_meals=profile.liked_meals[-MAX_LIST_ITEMS:],
            disliked_meals=profile.disliked_meals[-MAX_LIST_ITEMS:],
            liked_ingredients=profile.liked_ingredients[-MAX_LIST_ITEMS:],
            disliked_ingredients=profile.disliked_ingredients[-MAX_LIST_ITEMS:],
            constraints=list(profile.constraints),
            notes=profile.notes[-MAX_NOTES_CHARS:] if profile.notes else "",
        )

    def summarize_feedback_batch(
        self,
        feedback_list: list[MealFeedback],
        existing: PreferenceProfile | None = None,
    ) -> str:
        """Produce a short text summary of feedback for storage / prompts."""
        if not feedback_list:
            return existing.notes if existing and existing.notes else ""

        lines = []
        for fb in feedback_list[-30:]:
            comment = f" — {fb.comment}" if fb.comment else ""
            lines.append(f"{fb.rating.value}: {fb.meal_title}{comment}")

        batch_text = "\n".join(lines)
        if len(batch_text) <= MAX_NOTES_CHARS:
            return batch_text

        try:
            prompt = f"""Summarize this meal feedback into 2-4 bullet points (max 400 chars).
Focus on likes, dislikes, and recurring patterns. No preamble.

Feedback:
{batch_text[:2000]}"""
            summary = self.llm.chat(
                [{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=256,
            )
            return summary.strip()[:MAX_NOTES_CHARS]
        except OllamaUnavailableError:
            logger.debug("LLM unavailable for feedback summary; using heuristic")
            return self._heuristic_feedback_summary(feedback_list)

    def summarize_plan(self, plan: WeeklyPlan) -> str:
        """One-line-per-dinner summary of a weekly plan."""
        lines = [f"Week {plan.week_start} — {plan.week_end} ({plan.status.value})"]
        dinners = [m for m in plan.meals if m.meal_block == "adult_dinner"]
        for meal in dinners[:7]:
            ings = ", ".join(i.name for i in meal.recipe.ingredients[:4])
            lines.append(f"{meal.day[:3]}: {meal.recipe.title} [{ings}]")
        if plan.synergy_notes:
            lines.append(f"Synergy: {plan.synergy_notes[:120]}")
        return "\n".join(lines)

    def merge_notes(self, existing: str, new_summary: str, max_chars: int = MAX_NOTES_CHARS) -> str:
        combined = f"{existing.strip()}\n{new_summary.strip()}".strip() if existing else new_summary.strip()
        if len(combined) <= max_chars:
            return combined
        return combined[-max_chars:]

    def _heuristic_feedback_summary(self, feedback_list: list[MealFeedback]) -> str:
        loved = [f.meal_title for f in feedback_list if f.rating.value in ("loved", "liked")]
        disliked = [f.meal_title for f in feedback_list if f.rating.value in ("disliked", "reject")]
        parts = []
        if loved:
            parts.append(f"Liked: {', '.join(dict.fromkeys(loved[-8:]))}")
        if disliked:
            parts.append(f"Disliked: {', '.join(dict.fromkeys(disliked[-8:]))}")
        return "; ".join(parts)[:MAX_NOTES_CHARS]
