from __future__ import annotations

import re
from dataclasses import dataclass, field

from mealprepper.config import Settings, get_settings
from mealprepper.models.meals import MealRecipe
from mealprepper.skills.pantry_config import _normalize_name

FOOD_GROUP_ORDER = ("carb", "protein", "veggie", "fruit", "fat")


@dataclass
class FoodGroupCoverage:
    """Which food groups are covered by a meal's ingredients."""

    covered: dict[str, str] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.missing


class FoodGroupsSkill:
    """Classify meal ingredients into food groups and note balanced toddler plates."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        raw = self.settings.load_yaml("food_groups.yaml")
        self.required_groups: list[str] = list(raw.get("required_groups", FOOD_GROUP_ORDER))
        self.fallback_additions: dict[str, str] = {
            str(key): str(value)
            for key, value in (raw.get("fallback_additions") or {}).items()
        }
        self.group_keywords: dict[str, list[str]] = {}
        for group, keywords in (raw.get("groups") or {}).items():
            self.group_keywords[str(group)] = [_normalize_name(word) for word in keywords]

    def strict_for_block(self, meal_block: str) -> bool:
        return meal_block.startswith("toddler")

    def note_for_block(self, meal_block: str) -> bool:
        if meal_block.startswith("infant") or meal_block == "bulk_meal_prep":
            return False
        return meal_block.startswith("toddler") or meal_block.startswith("adult")

    def prompt_context(self, *, strict: bool) -> str:
        groups = ", ".join(self.required_groups)
        if strict:
            return (
                f"Each toddler meal must cover all food groups: {groups}.\n"
                "Include ingredients (or sides) for every group in key_ingredients and the recipe.\n"
                "Set food_groups in recipe JSON to map each group to its ingredient.\n"
                "If the main dish lacks a group, add a simple side (e.g. apple slices, carrot sticks, avocado)."
            )
        return (
            f"Note food groups when sensible: {groups}.\n"
            "Set food_groups in recipe JSON when ingredients clearly map to groups."
        )

    def classify_ingredient(self, name: str) -> set[str]:
        normalized = _normalize_name(name)
        matched: set[str] = set()
        for group in FOOD_GROUP_ORDER:
            keywords = self.group_keywords.get(group, [])
            if any(keyword in normalized or normalized in keyword for keyword in keywords):
                matched.add(group)
        return matched

    def analyze(self, title: str, ingredients: list[str]) -> FoodGroupCoverage:
        covered: dict[str, str] = {}
        search_terms = [_normalize_name(title)] + [_normalize_name(item) for item in ingredients]

        for group in self.required_groups:
            keywords = self.group_keywords.get(group, [])
            for term in search_terms:
                if not term:
                    continue
                for keyword in keywords:
                    if keyword in term or term in keyword:
                        covered[group] = self._display_name(term, ingredients)
                        break
                if group in covered:
                    break

        missing = [group for group in self.required_groups if group not in covered]
        return FoodGroupCoverage(covered=covered, missing=missing)

    def format_coverage(self, coverage: FoodGroupCoverage) -> str:
        parts = []
        for group in self.required_groups:
            value = coverage.covered.get(group, "—")
            parts.append(f"{group.title()}: {value}")
        return " | ".join(parts)

    def suggest_additions(self, missing: list[str]) -> list[str]:
        suggestions: list[str] = []
        for group in missing:
            fallback = self.fallback_additions.get(group)
            if fallback:
                suggestions.append(f"{fallback} ({group})")
        return suggestions

    def annotate_recipe(self, recipe: MealRecipe, meal_block: str) -> MealRecipe:
        if not self.note_for_block(meal_block):
            return recipe

        ingredient_names = [item.name for item in recipe.ingredients]
        coverage = self.analyze(recipe.title, ingredient_names)

        if recipe.food_groups:
            for group, value in recipe.food_groups.items():
                if value and group in self.required_groups:
                    coverage.covered.setdefault(group, value)
            coverage.missing = [
                group for group in self.required_groups if group not in coverage.covered
            ]

        recipe.food_groups = {
            group: coverage.covered.get(group, "")
            for group in self.required_groups
        }

        note = self.format_coverage(coverage)
        if self.strict_for_block(meal_block) and coverage.missing:
            additions = self.suggest_additions(coverage.missing)
            suffix = f" Add: {', '.join(additions)}." if additions else ""
            group_note = f"Food groups — {note}.{suffix}"
            recipe.toddler_modifications = self._append_note(recipe.toddler_modifications, group_note)
        elif recipe.food_groups and any(recipe.food_groups.values()):
            group_note = f"Food groups — {note}."
            if self.strict_for_block(meal_block):
                recipe.toddler_modifications = self._append_note(
                    recipe.toddler_modifications, group_note
                )

        return recipe

    @staticmethod
    def _display_name(term: str, ingredients: list[str]) -> str:
        for ingredient in ingredients:
            if _normalize_name(ingredient) == term:
                return ingredient
        cleaned = re.sub(r"\s+", " ", term).strip()
        return cleaned.title() if cleaned else term

    @staticmethod
    def _append_note(existing: str, addition: str) -> str:
        if not existing:
            return addition
        if addition in existing:
            return existing
        return f"{existing.rstrip('.')}. {addition}"
