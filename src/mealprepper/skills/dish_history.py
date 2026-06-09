from __future__ import annotations

from datetime import date

from mealprepper.config import Settings, get_settings
from mealprepper.storage.sqlite import SQLiteStore


def normalize_title(title: str) -> str:
    return title.strip().lower()


class DishHistorySkill:
    """Track recently served dishes and format exclusion context for planning."""

    def __init__(self, store: SQLiteStore | None = None, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.store = store or SQLiteStore(settings=self.settings)
        planning_cfg = self.settings.merged_config().get("planning", {})
        self.lookback_weeks = int(planning_cfg.get("dish_lookback_weeks", 2))

    def recent_titles_by_block(
        self,
        week_start: date,
        lookback_weeks: int | None = None,
    ) -> dict[str, set[str]]:
        return self.store.recent_dishes_by_block(
            week_start,
            lookback_weeks=lookback_weeks or self.lookback_weeks,
        )

    def normalized_exclusions(self, excluded: dict[str, set[str]]) -> dict[str, set[str]]:
        return {
            block: {normalize_title(title) for title in titles}
            for block, titles in excluded.items()
            if titles
        }

    def format_exclusions(self, excluded: dict[str, set[str]]) -> str:
        if not excluded:
            return ""

        priority_blocks = (
            "adult_dinner",
            "adult_lunch",
            "toddler_school_lunch",
            "toddler_weekend_lunch",
            "toddler_breakfast",
            "adult_breakfast",
            "infant_blw",
            "bulk_meal_prep",
        )
        lines = [
            "Do NOT repeat these meal titles this week — pick different dishes for variety.",
            f"(Served in the previous {self.lookback_weeks} week(s).)",
        ]
        seen_blocks: set[str] = set()
        for block in priority_blocks:
            titles = excluded.get(block)
            if not titles:
                continue
            seen_blocks.add(block)
            label = block.replace("_", " ")
            sorted_titles = sorted(titles)
            lines.append(f"- {label}: {', '.join(sorted_titles)}")

        for block in sorted(excluded):
            if block in seen_blocks:
                continue
            titles = excluded[block]
            if titles:
                lines.append(f"- {block.replace('_', ' ')}: {', '.join(sorted(titles))}")

        return "\n".join(lines)
