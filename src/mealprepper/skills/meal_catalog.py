from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any

from mealprepper.config import Settings, get_settings
from mealprepper.skills.dish_history import normalize_title
from mealprepper.skills.meal_blocks import DAYS, WEEKDAY_SCHOOL_BLOCKS, WeekMealOutline


class MealCatalog:
    """Curated per-block meals for fallback planning and variety enforcement."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self._catalog: dict[str, list[dict[str, Any]]] = self.settings.load_yaml("meal_catalog.yaml")

    def options_for_block(self, block: str) -> list[dict[str, Any]]:
        return list(self._catalog.get(block, []))

    def pick_for_slot(
        self,
        block: str,
        day_index: int,
        title_counts: Counter[str],
        max_repeat: int,
        excluded_titles: set[str] | None = None,
    ) -> dict[str, Any]:
        options = self.options_for_block(block)
        if not options:
            return {
                "title": f"Simple {block.replace('_', ' ').title()}",
                "key_ingredients": [],
                "prep_minutes": 20,
            }

        excluded = excluded_titles or set()
        rotated = options[day_index % len(options) :] + options[: day_index % len(options)]
        for option in rotated:
            title = str(option["title"])
            if normalize_title(title) in excluded:
                continue
            if title_counts[title] < max_repeat:
                return option

        for option in rotated:
            title = str(option["title"])
            if normalize_title(title) not in excluded:
                return option

        least_used = min(options, key=lambda opt: title_counts[str(opt["title"])])
        return least_used

    def build_fallback_outline(self, week_start: date, max_repeat: int = 2) -> list[WeekMealOutline]:
        outlines: list[WeekMealOutline] = []
        title_counts: Counter[str] = Counter()

        for day_index, day in enumerate(DAYS):
            for block in WEEKDAY_SCHOOL_BLOCKS[day]:
                option = self.pick_for_slot(block, day_index, title_counts, max_repeat)
                title = str(option["title"])
                title_counts[title] += 1
                outlines.append(
                    WeekMealOutline(
                        day=day,
                        meal_block=block,
                        title=title,
                        key_ingredients=list(option.get("key_ingredients", [])),
                        prep_minutes=int(option.get("prep_minutes", 20)),
                    )
                )
        return outlines

    def replace_overused(
        self,
        outline: WeekMealOutline,
        title_counts: Counter[str],
        max_repeat: int,
        day_index: int,
        excluded_titles: set[str] | None = None,
    ) -> WeekMealOutline:
        if title_counts[outline.title] < max_repeat:
            return outline
        option = self.pick_for_slot(
            outline.meal_block,
            day_index,
            title_counts,
            max_repeat=max_repeat - 1,
            excluded_titles=excluded_titles,
        )
        return WeekMealOutline(
            day=outline.day,
            meal_block=outline.meal_block,
            title=str(option["title"]),
            key_ingredients=list(option.get("key_ingredients", [])),
            prep_minutes=int(option.get("prep_minutes", outline.prep_minutes)),
        )

    def replace_excluded(
        self,
        outline: WeekMealOutline,
        title_counts: Counter[str],
        max_repeat: int,
        day_index: int,
        excluded_titles: set[str],
    ) -> WeekMealOutline:
        option = self.pick_for_slot(
            outline.meal_block,
            day_index,
            title_counts,
            max_repeat=max_repeat,
            excluded_titles=excluded_titles,
        )
        return WeekMealOutline(
            day=outline.day,
            meal_block=outline.meal_block,
            title=str(option["title"]),
            key_ingredients=list(option.get("key_ingredients", [])),
            prep_minutes=int(option.get("prep_minutes", outline.prep_minutes)),
        )

    def pick_unused(
        self,
        block: str,
        used_titles: set[str],
        day_index: int,
        excluded_titles: set[str] | None = None,
    ) -> dict[str, Any] | None:
        options = self.options_for_block(block)
        if not options:
            return None
        excluded = excluded_titles or set()
        rotated = options[day_index % len(options) :] + options[: day_index % len(options)]
        for option in rotated:
            title = str(option["title"])
            if title in used_titles:
                continue
            if normalize_title(title) in excluded:
                continue
            return option
        return None

    def prompt_style_guide(self) -> str:
        """Style hints for the LLM — intentionally excludes exact catalog titles."""
        return """Create original meal titles appropriate for each block (do NOT copy a fixed list):
- toddler_school_lunch: cold packable lunches (wraps, pinwheels, pasta salad, bento)
- toddler_weekend_lunch: quick home lunches
- toddler_breakfast: kid breakfasts
- adult_breakfast: adult breakfasts (can differ from toddler)
- adult_lunch: work lunches, bowls, soups, salads
- adult_dinner: rotate 4-5 different family dinners across Mon-Fri (never the same dinner nightly)
- infant_blw: age-appropriate soft finger foods only
- bulk_meal_prep: batch proteins/grains/veg for Saturday only"""
