from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field

from mealprepper.config import Settings, get_settings
from mealprepper.models.plans import WeeklyPlan
from mealprepper.skills.meal_blocks import DAYS, WeekMealOutline
from mealprepper.skills.meal_catalog import MealCatalog
from mealprepper.skills.pantry_config import _normalize_name
from mealprepper.skills.week_outline import outline_sort_key

logger = logging.getLogger(__name__)


@dataclass
class ShelfLifeCategory:
    name: str
    fridge_days: int
    keywords: list[str]


@dataclass
class ShelfLifeViolation:
    source_day: str
    source_block: str
    target_day: str
    target_block: str
    title: str
    day_gap: int
    max_days: int
    category: str
    message: str


@dataclass
class ShelfLifeAudit:
    violations: list[ShelfLifeViolation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.violations


class FoodShelfLifeSkill:
    """Validate meal reuse against realistic cooked-food fridge life."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.categories = self._load_categories()

    def max_fridge_days(self, title: str, ingredients: list[str] | None = None) -> int:
        category = self.classify_meal(title, ingredients or [])
        return category.fridge_days

    def classify_meal(self, title: str, ingredients: list[str] | None = None) -> ShelfLifeCategory:
        text = _normalize_name(title)
        if ingredients:
            text = f"{text} {' '.join(_normalize_name(item) for item in ingredients)}"

        for category in self.categories:
            if category.name == "default":
                continue
            if any(keyword in text for keyword in category.keywords):
                return category
        return self._default_category()

    def day_gap(self, source_day: str, target_day: str) -> int:
        return DAYS.index(target_day.lower()) - DAYS.index(source_day.lower())

    def reuse_is_valid(
        self,
        title: str,
        source_day: str,
        target_day: str,
        ingredients: list[str] | None = None,
    ) -> bool:
        gap = self.day_gap(source_day, target_day)
        if gap <= 0:
            return False
        return gap <= self.max_fridge_days(title, ingredients)

    def validate_outlines(
        self,
        outlines: list[WeekMealOutline],
        catalog: MealCatalog | None = None,
    ) -> list[WeekMealOutline]:
        catalog = catalog or MealCatalog(self.settings)
        updated = {self._key(outline): outline.model_copy() for outline in outlines}
        title_counts = Counter(outline.title for outline in outlines)
        fixed = 0

        for outline in list(updated.values()):
            if not outline.reuse_of_day or not outline.reuse_of_block:
                continue

            source = updated.get((outline.reuse_of_day, outline.reuse_of_block))
            ingredients = list(outline.key_ingredients or (source.key_ingredients if source else []))
            gap = self.day_gap(outline.reuse_of_day, outline.day)
            max_days = self.max_fridge_days(outline.title, ingredients)
            category = self.classify_meal(outline.title, ingredients)

            if gap <= max_days:
                continue

            violation = ShelfLifeViolation(
                source_day=outline.reuse_of_day,
                source_block=outline.reuse_of_block,
                target_day=outline.day,
                target_block=outline.meal_block,
                title=outline.title,
                day_gap=gap,
                max_days=max_days,
                category=category.name,
                message=(
                    f"{outline.title} ({category.name}, {max_days}d fridge) cannot be reused "
                    f"{gap} days after {outline.reuse_of_day} — replaced with fresh meal"
                ),
            )
            logger.warning("Shelf life fix: %s", violation.message)

            day_index = DAYS.index(outline.day)
            replacement = catalog.pick_for_slot(
                outline.meal_block,
                day_index,
                title_counts,
                max_repeat=99,
            )
            new_title = str(replacement["title"])
            title_counts[outline.title] -= 1
            title_counts[new_title] += 1

            updated[self._key(outline)] = WeekMealOutline(
                day=outline.day,
                meal_block=outline.meal_block,
                title=new_title,
                key_ingredients=list(replacement.get("key_ingredients", [])),
                prep_minutes=int(replacement.get("prep_minutes", outline.prep_minutes)),
            )
            fixed += 1

        if fixed:
            logger.info("Shelf life validation replaced %d unsafe reuse slots", fixed)
        return sorted(updated.values(), key=outline_sort_key)

    def audit_plan(self, plan: WeeklyPlan) -> ShelfLifeAudit:
        audit = ShelfLifeAudit()
        for meal in plan.meals:
            if not meal.cook_source_day or not meal.cook_source_block:
                continue
            ingredients = [ing.name for ing in meal.recipe.ingredients]
            gap = self.day_gap(meal.cook_source_day, meal.day)
            max_days = self.max_fridge_days(meal.recipe.title, ingredients)
            category = self.classify_meal(meal.recipe.title, ingredients)
            if gap <= max_days:
                continue
            audit.violations.append(
                ShelfLifeViolation(
                    source_day=meal.cook_source_day,
                    source_block=meal.cook_source_block,
                    target_day=meal.day,
                    target_block=meal.meal_block,
                    title=meal.recipe.title,
                    day_gap=gap,
                    max_days=max_days,
                    category=category.name,
                    message=(
                        f"{meal.day.title()} {meal.meal_block.replace('_', ' ')} uses "
                        f"{meal.recipe.title} from {meal.cook_source_day} ({gap} days later) — "
                        f"{category.name} keeps ~{max_days} days"
                    ),
                )
            )
        audit.warnings = [v.message for v in audit.violations]
        return audit

    def render_audit(self, plan: WeeklyPlan, audit: ShelfLifeAudit | None = None) -> str:
        audit = audit or self.audit_plan(plan)
        lines = [
            f"# Food Shelf Life — {plan.week_start} — {plan.week_end}",
            "",
        ]
        if audit.ok:
            lines.append("_No leftover timing issues detected._")
            lines.append("")
            lines.append("Reuse rules in effect:")
            for category in self.categories:
                if category.name == "default":
                    continue
                keywords = ", ".join(category.keywords[:4])
                lines.append(
                    f"- **{category.name.replace('_', ' ').title()}** (~{category.fridge_days} days): {keywords}"
                )
            return "\n".join(lines).strip() + "\n"

        lines.append("## Issues")
        for violation in audit.violations:
            lines.append(f"- {violation.message}")
        lines.append("")
        lines.append("_Regenerate the plan to auto-fix outline-level reuse._")
        return "\n".join(lines).strip() + "\n"

    def _load_categories(self) -> list[ShelfLifeCategory]:
        raw = self.settings.load_yaml("food_shelf_life.yaml")
        categories: list[ShelfLifeCategory] = []
        for name, data in raw.get("categories", {}).items():
            if not isinstance(data, dict):
                continue
            categories.append(
                ShelfLifeCategory(
                    name=name,
                    fridge_days=int(data.get("fridge_days", 3)),
                    keywords=[_normalize_name(word) for word in data.get("keywords", [])],
                )
            )
        categories.sort(key=lambda item: 0 if item.name == "default" else 1)
        return categories

    def _default_category(self) -> ShelfLifeCategory:
        for category in self.categories:
            if category.name == "default":
                return category
        return ShelfLifeCategory(name="default", fridge_days=3, keywords=[])

    @staticmethod
    def _key(outline: WeekMealOutline) -> tuple[str, str]:
        return (outline.day, outline.meal_block)
