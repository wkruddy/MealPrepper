from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from mealprepper.config import Settings, get_settings
from mealprepper.models.family import FamilyMember, FamilyProfile


@dataclass
class BLWBracket:
    label: str
    guidance: str
    allowed_foods: list[str]
    caution_foods: list[str]
    avoid_foods: list[str]


class BLWSafety:
    """Age-based BLW food validation and prompt context."""

    def __init__(
        self,
        family: FamilyProfile,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.family = family
        self.infant = family.infant()
        self.bracket = self._load_bracket(self.infant.age_months if self.infant else 7.5)

    def _load_bracket(self, age_months: float) -> BLWBracket:
        raw = self.settings.load_yaml("blw.yaml")
        brackets = raw.get("age_brackets", [])
        chosen = brackets[-1] if brackets else {}
        for entry in brackets:
            if age_months <= float(entry.get("max_months", 999)):
                chosen = entry
                break
        return BLWBracket(
            label=str(chosen.get("label", "6-8 months")),
            guidance=str(chosen.get("guidance", "")).strip(),
            allowed_foods=[str(x).lower() for x in chosen.get("allowed_foods", [])],
            caution_foods=[str(x).lower() for x in chosen.get("caution_foods", [])],
            avoid_foods=[str(x).lower() for x in chosen.get("avoid_foods", [])],
        )

    def prompt_context(self) -> str:
        infant = self.infant
        age = infant.age_months if infant else 7.5
        allowed = ", ".join(self.bracket.allowed_foods[:16])
        avoid = ", ".join(self.bracket.avoid_foods[:12])
        caution = ", ".join(self.bracket.caution_foods[:8])
        return (
            f"Infant age: {age} months ({self.bracket.label}).\n"
            f"{self.bracket.guidance}\n"
            f"Allowed BLW foods: {allowed}.\n"
            f"Introduce with caution (allergens): {caution}.\n"
            f"Never offer: {avoid}."
        )

    def validate_meal(
        self,
        title: str,
        ingredients: list[str],
    ) -> tuple[list[str], list[str], str]:
        """Return warnings, blocked ingredients, and infant serving guidance."""
        warnings: list[str] = []
        blocked: list[str] = []
        normalized = [self._normalize_ingredient(name) for name in ingredients]

        for ing in normalized:
            if self._matches_any(ing, self.bracket.avoid_foods):
                blocked.append(ing)
                warnings.append(f"Avoid for {self.bracket.label}: {ing}")
            elif self._matches_any(ing, self.bracket.caution_foods):
                warnings.append(f"Introduce cautiously if not yet offered: {ing}")

        if blocked:
            guidance = (
                f"Do not serve as written for infant BLW ({self.bracket.label}). "
                f"Replace or omit: {', '.join(blocked)}. "
                f"Offer soft, squishable finger strips instead."
            )
        else:
            guidance = self._build_guidance(title, normalized)

        return warnings, blocked, guidance

    def infant_guidance_for_outline(self, title: str, key_ingredients: list[str]) -> str:
        _, blocked, guidance = self.validate_meal(title, key_ingredients)
        if blocked:
            return guidance
        return self._build_guidance(title, [self._normalize_ingredient(i) for i in key_ingredients])

    def _build_guidance(self, title: str, ingredients: list[str]) -> str:
        prep_hints = []
        for ing in ingredients:
            if ing in {"broccoli", "green beans", "sweet potato", "carrot"}:
                prep_hints.append(f"Steam {ing} until very soft; offer as finger-length strips.")
            elif ing in {"banana", "avocado", "pear", "mango"}:
                prep_hints.append(f"Offer ripe {ing} in spears or mashed lightly.")
            elif ing in {"salmon", "chicken", "turkey", "beef", "egg", "eggs"}:
                prep_hints.append(f"Cook {ing} until fully done; flake/shred into soft strips.")
        if not prep_hints:
            prep_hints.append(
                "Offer soft, finger-length pieces the infant can grasp; food should squish easily."
            )
        return " ".join(prep_hints[:2])

    @staticmethod
    def _normalize_ingredient(name: str) -> str:
        cleaned = re.sub(r"[^a-z0-9\s]", " ", name.lower())
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if cleaned.endswith("s") and cleaned[:-1] in {"egg", "pea", "bean"}:
            return cleaned[:-1]
        return cleaned

    @staticmethod
    def _matches_any(ingredient: str, terms: list[str]) -> bool:
        return any(term in ingredient or ingredient in term for term in terms)
