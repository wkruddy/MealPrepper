from __future__ import annotations

import logging
import re
from collections import Counter
from datetime import date
from typing import Any

from pydantic import ValidationError

from mealprepper.skills.dish_history import normalize_title
from mealprepper.skills.meal_catalog import MealCatalog
from mealprepper.skills.meal_blocks import DAYS, WEEKDAY_SCHOOL_BLOCKS, WeekMealOutline

logger = logging.getLogger(__name__)

BLOCK_ALIASES = {
    "school lunch": "toddler_school_lunch",
    "toddler lunch": "toddler_school_lunch",
    "weekend lunch": "toddler_weekend_lunch",
    "toddler breakfast": "toddler_breakfast",
    "adult breakfast": "adult_breakfast",
    "adult lunch": "adult_lunch",
    "adult dinner": "adult_dinner",
    "dinner": "adult_dinner",
    "infant": "infant_blw",
    "infant blw": "infant_blw",
    "blw": "infant_blw",
    "bulk prep": "bulk_meal_prep",
    "meal prep": "bulk_meal_prep",
}


def required_slots() -> dict[tuple[str, str], None]:
    slots: dict[tuple[str, str], None] = {}
    for day, blocks in WEEKDAY_SCHOOL_BLOCKS.items():
        for block in blocks:
            slots[(day, block)] = None
    return slots


def outline_sort_key(outline: WeekMealOutline) -> tuple[int, int]:
    day_idx = DAYS.index(outline.day)
    blocks = WEEKDAY_SCHOOL_BLOCKS[outline.day]
    try:
        block_idx = blocks.index(outline.meal_block)
    except ValueError:
        block_idx = len(blocks)
    return (day_idx, block_idx)


def remap_block_for_day(day: str, block: str) -> str | None:
    valid_blocks = WEEKDAY_SCHOOL_BLOCKS[day]
    if block in valid_blocks:
        return block
    if block == "bulk_meal_prep":
        return None
    if block == "toddler_school_lunch" and day in {"saturday", "sunday"}:
        return "toddler_weekend_lunch"
    if block == "toddler_weekend_lunch" and day not in {"saturday", "sunday"}:
        return "toddler_school_lunch"
    return None


def sanitize_outlines(outlines: list[WeekMealOutline]) -> list[WeekMealOutline]:
    """Drop or remap LLM slots that don't belong on a given day."""
    cleaned: list[WeekMealOutline] = []
    seen: set[tuple[str, str]] = set()

    for outline in outlines:
        block = remap_block_for_day(outline.day, outline.meal_block)
        if block is None:
            logger.info(
                "Dropped invalid slot: %s %s (%s)",
                outline.day,
                outline.meal_block,
                outline.title,
            )
            continue

        key = (outline.day, block)
        if key in seen:
            logger.debug("Skipping duplicate slot: %s %s", outline.day, block)
            continue
        seen.add(key)

        if block != outline.meal_block:
            outline = WeekMealOutline(
                day=outline.day,
                meal_block=block,
                title=outline.title,
                key_ingredients=outline.key_ingredients,
                prep_minutes=outline.prep_minutes,
            )

        cleaned.append(outline)

    return sorted(cleaned, key=outline_sort_key)


def normalize_day(value: Any, week_start: date) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in DAYS:
        return text
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        parsed = date.fromisoformat(text)
        offset = (parsed - week_start).days
        if 0 <= offset < 7:
            return DAYS[offset]
    weekday_names = {
        "mon": "monday",
        "tue": "tuesday",
        "wed": "wednesday",
        "thu": "thursday",
        "fri": "friday",
        "sat": "saturday",
        "sun": "sunday",
    }
    return weekday_names.get(text[:3], None)


def normalize_block(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    if text in WEEKDAY_SCHOOL_BLOCKS["monday"] or text in WEEKDAY_SCHOOL_BLOCKS["saturday"]:
        return text
    alias = BLOCK_ALIASES.get(text.replace("_", " "))
    if alias:
        return alias
    for block in WEEKDAY_SCHOOL_BLOCKS["saturday"]:
        if block in text or text in block:
            return block
    return None


def normalize_outline_item(raw: dict[str, Any], week_start: date) -> dict[str, Any] | None:
    day = normalize_day(raw.get("day"), week_start)
    meal_block = normalize_block(raw.get("meal_block") or raw.get("block") or raw.get("meal_type"))
    title = raw.get("title") or raw.get("meal") or raw.get("name")
    if not day or not meal_block or not title:
        return None
    return {
        "day": day,
        "meal_block": meal_block,
        "title": str(title).strip(),
        "key_ingredients": raw.get("key_ingredients") or raw.get("ingredients") or [],
        "prep_minutes": int(raw.get("prep_minutes") or raw.get("prep_time") or 20),
    }


def parse_outline_items(raw_items: list[Any], week_start: date) -> list[WeekMealOutline]:
    outlines: list[WeekMealOutline] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        normalized = normalize_outline_item(raw, week_start)
        if not normalized:
            continue
        try:
            outlines.append(WeekMealOutline.model_validate(normalized))
        except ValidationError as exc:
            logger.debug("Skipping invalid outline item: %s", exc)
    return outlines


def fill_missing_slots(
    outlines: list[WeekMealOutline],
    week_start: date,
    catalog: MealCatalog,
    max_repeat: int,
    excluded_by_block: dict[str, set[str]] | None = None,
) -> list[WeekMealOutline]:
    filled = list(outlines)
    present = {(o.day, o.meal_block) for o in filled}
    title_counts = Counter(o.title for o in filled)
    excluded_by_block = excluded_by_block or {}

    for day_index, day in enumerate(DAYS):
        for block in WEEKDAY_SCHOOL_BLOCKS[day]:
            if (day, block) in present:
                continue
            option = catalog.pick_for_slot(
                block,
                day_index,
                title_counts,
                max_repeat,
                excluded_titles=excluded_by_block.get(block),
            )
            title = str(option["title"])
            title_counts[title] += 1
            filled.append(
                WeekMealOutline(
                    day=day,
                    meal_block=block,
                    title=title,
                    key_ingredients=list(option.get("key_ingredients", [])),
                    prep_minutes=int(option.get("prep_minutes", 20)),
                )
            )
            logger.info("Filled missing slot: %s %s -> %s", day, block, title)

    return sorted(filled, key=outline_sort_key)


def enforce_variety(
    outlines: list[WeekMealOutline],
    catalog: MealCatalog,
    max_repeat: int,
    max_consecutive: int = 2,
    excluded_by_block: dict[str, set[str]] | None = None,
) -> list[WeekMealOutline]:
    """Limit repeats per meal block and break long streaks of identical meals."""
    excluded_by_block = excluded_by_block or {}
    by_block: dict[str, list[WeekMealOutline]] = {}
    for outline in outlines:
        by_block.setdefault(outline.meal_block, []).append(outline)

    adjusted: list[WeekMealOutline] = []
    for block, block_outlines in by_block.items():
        block_outlines = sorted(block_outlines, key=lambda o: DAYS.index(o.day))
        title_counts: Counter[str] = Counter()
        previous_titles: list[str] = []
        excluded = excluded_by_block.get(block, set())

        for day_index, outline in enumerate(block_outlines):
            if title_counts[outline.title] >= max_repeat:
                outline = catalog.replace_overused(
                    outline,
                    title_counts,
                    max_repeat,
                    day_index,
                    excluded_titles=excluded,
                )
            if (
                len(previous_titles) >= max_consecutive
                and len(set(previous_titles[-max_consecutive:])) == 1
                and previous_titles[-1] == outline.title
            ):
                outline = catalog.replace_overused(
                    outline,
                    title_counts,
                    max_repeat,
                    day_index,
                    excluded_titles=excluded,
                )

            title_counts[outline.title] += 1
            previous_titles.append(outline.title)
            adjusted.append(outline)

    return sorted(adjusted, key=outline_sort_key)


def enforce_cross_week_exclusion(
    outlines: list[WeekMealOutline],
    catalog: MealCatalog,
    excluded_by_block: dict[str, set[str]],
    max_repeat: int,
) -> list[WeekMealOutline]:
    """Swap meals that were served in recent prior weeks."""
    if not excluded_by_block:
        return outlines

    by_block: dict[str, list[WeekMealOutline]] = {}
    for outline in outlines:
        by_block.setdefault(outline.meal_block, []).append(outline)

    replacements: dict[tuple[str, str], WeekMealOutline] = {}
    for block, block_outlines in by_block.items():
        excluded = excluded_by_block.get(block, set())
        if not excluded:
            continue

        block_outlines = sorted(block_outlines, key=lambda o: DAYS.index(o.day))
        title_counts = Counter(o.title for o in block_outlines)
        for day_index, outline in enumerate(block_outlines):
            if normalize_title(outline.title) not in excluded:
                continue
            replacement = catalog.replace_excluded(
                outline,
                title_counts,
                max_repeat,
                day_index,
                excluded,
            )
            if normalize_title(replacement.title) == normalize_title(outline.title):
                continue
            title_counts[outline.title] -= 1
            title_counts[replacement.title] += 1
            replacements[(outline.day, outline.meal_block)] = replacement
            logger.info(
                "Cross-week swap: %s %s %s -> %s",
                outline.day,
                block,
                outline.title,
                replacement.title,
            )

    if not replacements:
        return outlines

    return sorted(
        [replacements.get((o.day, o.meal_block), o) for o in outlines],
        key=outline_sort_key,
    )


def ensure_minimum_variety(
    outlines: list[WeekMealOutline],
    catalog: MealCatalog,
    min_unique: int = 3,
    excluded_by_block: dict[str, set[str]] | None = None,
) -> list[WeekMealOutline]:
    """Swap repeated titles so each block has at least min_unique meals when possible."""
    if min_unique <= 1:
        return outlines

    excluded_by_block = excluded_by_block or {}
    by_block: dict[str, list[WeekMealOutline]] = {}
    for outline in outlines:
        by_block.setdefault(outline.meal_block, []).append(outline)

    replacements: dict[tuple[str, str], WeekMealOutline] = {}

    for block, block_outlines in by_block.items():
        if block == "bulk_meal_prep":
            continue
        options = catalog.options_for_block(block)
        if len(options) < min_unique:
            continue

        block_outlines = sorted(block_outlines, key=lambda o: DAYS.index(o.day))
        used_titles = {o.title for o in block_outlines}
        if len(used_titles) >= min_unique:
            continue

        title_counts = Counter(o.title for o in block_outlines)
        for day_index, outline in enumerate(block_outlines):
            if len(used_titles) >= min_unique:
                break
            if title_counts[outline.title] <= 1:
                continue

            alternative = catalog.pick_unused(
                block,
                used_titles,
                day_index,
                excluded_titles=excluded_by_block.get(block),
            )
            if not alternative:
                break

            new_title = str(alternative["title"])
            used_titles.add(new_title)
            title_counts[outline.title] -= 1
            title_counts[new_title] += 1
            replacements[(outline.day, outline.meal_block)] = WeekMealOutline(
                day=outline.day,
                meal_block=outline.meal_block,
                title=new_title,
                key_ingredients=list(alternative.get("key_ingredients", [])),
                prep_minutes=int(alternative.get("prep_minutes", outline.prep_minutes)),
            )
            logger.info(
                "Variety swap: %s %s %s -> %s",
                outline.day,
                block,
                outline.title,
                new_title,
            )

    if not replacements:
        return outlines

    return sorted(
        [replacements.get((o.day, o.meal_block), o) for o in outlines],
        key=outline_sort_key,
    )


def log_outline_summary(outlines: list[WeekMealOutline]) -> None:
    by_block: dict[str, list[str]] = {}
    for outline in outlines:
        by_block.setdefault(outline.meal_block, []).append(outline.title)

    parts = []
    for block in sorted(by_block):
        titles = by_block[block]
        parts.append(f"{block}={len(set(titles))} unique")
    logger.info("Outline variety: %s", ", ".join(parts))

    dinners = [
        f"{day[:3]}:{next(o.title for o in outlines if o.day == day and o.meal_block == 'adult_dinner')}"
        for day in DAYS
        if any(o.day == day and o.meal_block == "adult_dinner" for o in outlines)
    ]
    logger.info("Adult dinners by day: %s", " | ".join(dinners))


def finalize_outline(
    outlines: list[WeekMealOutline],
    week_start: date,
    catalog: MealCatalog,
    max_repeat: int,
    min_unique: int = 3,
    cook_efficiency=None,
    excluded_by_block: dict[str, set[str]] | None = None,
) -> list[WeekMealOutline]:
    from mealprepper.skills.cook_efficiency import CookEfficiencyConfig, CookEfficiencySkill

    cfg = cook_efficiency or CookEfficiencyConfig.from_settings()
    normalized_excluded = excluded_by_block or {}
    sanitized = sanitize_outlines(outlines)
    filled = fill_missing_slots(
        sanitized,
        week_start,
        catalog,
        max_repeat,
        excluded_by_block=normalized_excluded,
    )
    varied = enforce_variety(
        filled,
        catalog,
        max_repeat=max_repeat,
        excluded_by_block=normalized_excluded,
    )
    varied = enforce_cross_week_exclusion(
        varied,
        catalog,
        normalized_excluded,
        max_repeat=max_repeat,
    )
    if cfg.enabled:
        varied = CookEfficiencySkill(cfg).apply_to_outlines(varied)
        min_unique = cfg.min_unique_per_block
    result = ensure_minimum_variety(
        varied,
        catalog,
        min_unique=min_unique,
        excluded_by_block=normalized_excluded,
    )
    from mealprepper.skills.food_shelf_life import FoodShelfLifeSkill

    result = FoodShelfLifeSkill().validate_outlines(result, catalog)
    log_outline_summary(result)
    return result
