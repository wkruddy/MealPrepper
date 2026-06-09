from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field

from mealprepper.config import Settings, get_settings
from mealprepper.skills.food_shelf_life import FoodShelfLifeSkill
from mealprepper.models.plans import WeeklyPlan
from mealprepper.skills.meal_blocks import DAYS, WeekMealOutline
from mealprepper.skills.week_outline import outline_sort_key

logger = logging.getLogger(__name__)


@dataclass
class CookEfficiencyConfig:
    enabled: bool = True
    min_unique_per_block: int = 2
    max_dinner_cook_sessions: int = 4
    cross_block_reuse: bool = True
    repeat_dinners: bool = True

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> CookEfficiencyConfig:
        settings = settings or get_settings()
        planning = settings.merged_config().get("planning", {})
        raw = planning.get("cook_efficiency", {})
        return cls(
            enabled=bool(raw.get("enabled", True)),
            min_unique_per_block=int(raw.get("min_unique_per_block", 2)),
            max_dinner_cook_sessions=int(raw.get("max_dinner_cook_sessions", 4)),
            cross_block_reuse=bool(raw.get("cross_block_reuse", True)),
            repeat_dinners=bool(raw.get("repeat_dinners", True)),
        )


@dataclass
class ReuseLink:
    source_day: str
    source_block: str
    target_day: str
    target_block: str
    title: str
    link_type: str  # leftover | repeat


@dataclass
class CookEfficiencyReport:
    reuse_links: list[ReuseLink] = field(default_factory=list)
    cook_sessions: dict[str, list[str]] = field(default_factory=dict)
    shared_ingredients: list[str] = field(default_factory=list)
    single_use_ingredients: list[str] = field(default_factory=list)
    synergy_notes: str = ""
    synergy_suggestions: list[str] = field(default_factory=list)
    estimated_cook_sessions: int = 0


class CookEfficiencySkill:
    """Reduce cook sessions by reusing meals across days and blocks."""

    LEFTOVER_LUNCH_BLOCKS = {"adult_lunch"}

    def __init__(
        self,
        config: CookEfficiencyConfig | None = None,
        shelf_life: FoodShelfLifeSkill | None = None,
    ) -> None:
        self.config = config or CookEfficiencyConfig.from_settings()
        self.shelf_life = shelf_life or FoodShelfLifeSkill()

    def apply_to_outlines(self, outlines: list[WeekMealOutline]) -> list[WeekMealOutline]:
        if not self.config.enabled:
            return outlines

        updated = {self._key(outline): outline.model_copy() for outline in outlines}

        if self.config.cross_block_reuse:
            updated = self._link_dinner_to_next_lunch(updated)

        if self.config.repeat_dinners:
            updated = self._reduce_dinner_sessions(updated)

        result = sorted(updated.values(), key=outline_sort_key)
        links = self._links_from_outlines(result)
        unique_dinner_cooks = len(
            {
                (outline.day, outline.title)
                for outline in result
                if outline.meal_block == "adult_dinner" and not outline.reuse_of_day
            }
        )
        logger.info(
            "Cook efficiency: %d reuse links, %d unique dinner cooks",
            len(links),
            unique_dinner_cooks,
        )
        return result

    def build_report(self, plan: WeeklyPlan) -> CookEfficiencyReport:
        counts = Counter(plan.ingredient_names())
        shared = sorted(name for name, count in counts.items() if count >= 2)
        singles = sorted(name for name, count in counts.items() if count == 1)

        reuse_links = self._links_from_meals(plan)
        cook_sessions = self._cook_sessions_from_plan(plan)

        dinner_cooks = cook_sessions.get("adult_dinner", [])
        breakfast_cooks = cook_sessions.get("adult_breakfast", [])
        estimated = len(dinner_cooks) + len(breakfast_cooks)

        return CookEfficiencyReport(
            reuse_links=reuse_links,
            cook_sessions=cook_sessions,
            shared_ingredients=shared[:20],
            single_use_ingredients=singles[:15],
            synergy_notes=plan.synergy_notes,
            synergy_suggestions=list(plan.synergy_suggestions),
            estimated_cook_sessions=estimated,
        )

    def render_report(self, plan: WeeklyPlan, report: CookEfficiencyReport | None = None) -> str:
        report = report or self.build_report(plan)
        lines = [
            f"# Cook Efficiency — {plan.week_start} — {plan.week_end}",
            "",
            f"**Estimated cook sessions:** ~{report.estimated_cook_sessions} "
            f"({len(report.cook_sessions.get('adult_dinner', []))} dinners, "
            f"{len(report.cook_sessions.get('adult_breakfast', []))} breakfasts to cook fresh)",
            "",
        ]

        if report.reuse_links:
            lines.append("## Meal reuse")
            lines.append("_Same food, fewer cooks:_")
            lines.append("")
            for link in report.reuse_links:
                src = f"{link.source_day.title()} {link.source_block.replace('_', ' ')}"
                tgt = f"{link.target_day.title()} {link.target_block.replace('_', ' ')}"
                kind = "Leftovers" if link.link_type == "leftover" else "Repeat cook"
                lines.append(f"- **{link.title}** — {kind}: {src} → {tgt}")
            lines.append("")

        if report.cook_sessions:
            lines.append("## What you actually cook")
            for block, titles in sorted(report.cook_sessions.items()):
                if not titles:
                    continue
                label = block.replace("_", " ").title()
                lines.append(f"### {label}")
                for title in titles:
                    lines.append(f"- {title}")
                lines.append("")

        if report.shared_ingredients:
            lines.append("## Shared ingredients")
            lines.append(", ".join(report.shared_ingredients))
            lines.append("")

        if report.single_use_ingredients:
            lines.append("## Single-use ingredients (waste risk)")
            lines.append(", ".join(report.single_use_ingredients[:10]))
            lines.append("")

        if report.synergy_notes or report.synergy_suggestions:
            lines.append("## Synergy notes")
            if report.synergy_notes:
                lines.append(report.synergy_notes)
            for suggestion in report.synergy_suggestions:
                lines.append(f"- {suggestion}")
            lines.append("")

        return "\n".join(lines).strip() + "\n"

    def _link_dinner_to_next_lunch(
        self, outlines: dict[tuple[str, str], WeekMealOutline]
    ) -> dict[tuple[str, str], WeekMealOutline]:
        for day_index in range(len(DAYS) - 1):
            src_day = DAYS[day_index]
            tgt_day = DAYS[day_index + 1]
            src_key = (src_day, "adult_dinner")
            tgt_key = (tgt_day, "adult_lunch")
            src = outlines.get(src_key)
            if not src or tgt_key not in outlines:
                continue

            tgt = outlines[tgt_key]
            outlines[tgt_key] = WeekMealOutline(
                day=tgt.day,
                meal_block=tgt.meal_block,
                title=src.title,
                key_ingredients=list(src.key_ingredients or tgt.key_ingredients),
                prep_minutes=min(tgt.prep_minutes, 10),
                reuse_of_day=src_day,
                reuse_of_block="adult_dinner",
                cook_note=f"Leftovers from {src_day.title()} dinner",
            )
            logger.info(
                "Cook reuse: %s dinner -> %s lunch (%s)",
                src_day,
                tgt_day,
                src.title,
            )
        return outlines

    def _reduce_dinner_sessions(
        self, outlines: dict[tuple[str, str], WeekMealOutline]
    ) -> dict[tuple[str, str], WeekMealOutline]:
        dinners = [
            outlines[(day, "adult_dinner")]
            for day in DAYS
            if (day, "adult_dinner") in outlines
        ]
        if len(dinners) <= self.config.max_dinner_cook_sessions:
            return outlines

        templates = dinners[: self.config.max_dinner_cook_sessions]
        for index, dinner in enumerate(dinners):
            if index < self.config.max_dinner_cook_sessions:
                continue
            template = self._pick_shelf_safe_template(dinner, templates)
            if template is None:
                logger.info(
                    "Shelf life: no safe dinner reuse for %s (%s) — keeping fresh cook",
                    dinner.day,
                    dinner.title,
                )
                continue
            key = (dinner.day, "adult_dinner")
            outlines[key] = WeekMealOutline(
                day=dinner.day,
                meal_block="adult_dinner",
                title=template.title,
                key_ingredients=list(template.key_ingredients or dinner.key_ingredients),
                prep_minutes=template.prep_minutes,
                reuse_of_day=template.day,
                reuse_of_block="adult_dinner",
                cook_note=f"Same as {template.day.title()} dinner — cook once, eat twice",
            )
            logger.info(
                "Dinner repeat: %s -> %s (%s)",
                template.day,
                dinner.day,
                template.title,
            )
        return outlines

    def _pick_shelf_safe_template(
        self,
        dinner: WeekMealOutline,
        templates: list[WeekMealOutline],
    ) -> WeekMealOutline | None:
        for template in templates:
            if self.shelf_life.reuse_is_valid(
                template.title,
                template.day,
                dinner.day,
                template.key_ingredients,
            ):
                return template
        return None

    def _links_from_outlines(self, outlines: list[WeekMealOutline]) -> list[ReuseLink]:
        links: list[ReuseLink] = []
        for outline in outlines:
            if not outline.reuse_of_day or not outline.reuse_of_block:
                continue
            link_type = "leftover" if outline.meal_block in self.LEFTOVER_LUNCH_BLOCKS else "repeat"
            links.append(
                ReuseLink(
                    source_day=outline.reuse_of_day,
                    source_block=outline.reuse_of_block,
                    target_day=outline.day,
                    target_block=outline.meal_block,
                    title=outline.title,
                    link_type=link_type,
                )
            )
        return links

    def _links_from_meals(self, plan: WeeklyPlan) -> list[ReuseLink]:
        links: list[ReuseLink] = []
        for meal in plan.meals:
            if not meal.cook_source_day or not meal.cook_source_block:
                continue
            link_type = "leftover" if meal.meal_block in self.LEFTOVER_LUNCH_BLOCKS else "repeat"
            links.append(
                ReuseLink(
                    source_day=meal.cook_source_day,
                    source_block=meal.cook_source_block,
                    target_day=meal.day,
                    target_block=meal.meal_block,
                    title=meal.recipe.title,
                    link_type=link_type,
                )
            )
        return links

    def _cook_sessions_from_plan(self, plan: WeeklyPlan) -> dict[str, list[str]]:
        sessions: dict[str, list[str]] = {}

        for meal in plan.meals:
            if meal.cook_source_day:
                continue
            block = meal.meal_block
            if block not in {"adult_dinner", "adult_breakfast", "bulk_meal_prep"}:
                continue
            title = meal.recipe.title
            if title not in sessions.setdefault(block, []):
                sessions[block].append(title)

        return sessions

    @staticmethod
    def _key(outline: WeekMealOutline) -> tuple[str, str]:
        return (outline.day, outline.meal_block)
