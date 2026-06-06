from __future__ import annotations

import logging

from mealprepper.context.compressor import ContextCompressor
from mealprepper.models.feedback import FeedbackRating, MealFeedback, PreferenceProfile
from mealprepper.storage.sqlite import SQLiteStore

logger = logging.getLogger(__name__)

POSITIVE = {FeedbackRating.LOVED, FeedbackRating.LIKED}
NEGATIVE = {FeedbackRating.DISLIKED, FeedbackRating.REJECT}


class PreferenceLearnerSkill:
    """Apply meal feedback to the family's preference profile."""

    def __init__(self, store: SQLiteStore | None = None) -> None:
        self.store = store or SQLiteStore()
        self.compressor = ContextCompressor()

    def apply_feedback(self, feedback_list: list[MealFeedback]) -> PreferenceProfile:
        profile = self.store.get_preferences()
        for fb in feedback_list:
            title = fb.meal_title.strip()
            if not title or title == "Unknown meal":
                continue
            if fb.rating in POSITIVE:
                if title not in profile.liked_meals:
                    profile.liked_meals.append(title)
                profile.disliked_meals = [m for m in profile.disliked_meals if m != title]
            elif fb.rating in NEGATIVE:
                if title not in profile.disliked_meals:
                    profile.disliked_meals.append(title)
                profile.liked_meals = [m for m in profile.liked_meals if m != title]
            elif fb.comment:
                profile.notes = (profile.notes + f"\n{fb.comment}").strip()

        profile = self.compressor.compress_profile(profile)
        summary = self.compressor.summarize_feedback_batch(feedback_list, existing=profile)
        if summary:
            profile.notes = self.compressor.merge_notes(profile.notes, summary)
            self.store.save_preference_summary(summary, feedback_count=len(feedback_list))

        self.store.save_preferences(profile)
        applied_ids = [f.id for f in feedback_list if f.id]
        if applied_ids:
            self.store.mark_feedback_applied(applied_ids)
        logger.info("Updated preferences from %d feedback items", len(feedback_list))
        return profile

    def process_unapplied(self) -> PreferenceProfile:
        pending = self.store.get_unapplied_feedback()
        if not pending:
            return self.store.get_compact_preferences()
        return self.apply_feedback(pending)
