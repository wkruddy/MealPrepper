from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field

from mealprepper.models.grocery import GroceryCategory, GroceryItem, GroceryList
from mealprepper.models.meals import Ingredient
from mealprepper.skills.pantry_config import PantryConfig, _normalize_name

logger = logging.getLogger(__name__)

_MEASUREMENT_UNITS = (
    r"cups?|tablespoons?|tbsp|teaspoons?|tsp|ounces?|oz|pounds?|lbs?|lb|"
    r"grams?|milliliters?|ml|liters?|l|cans?|envelopes?"
)
# Abbreviation "c" for cup only after a number (never bare — avoids stripping "Carrot", "Can", etc.)
_NUMERIC_QTY_RE = re.compile(
    rf"^((?:\d+(?:\.\d+)?\s+)*(?:\d+/\d+)?\s*)"
    rf"(?:(?:{_MEASUREMENT_UNITS})|c(?=\s))?\s+"
    rf"(.+)$",
    re.IGNORECASE,
)
def repair_ingredient(ingredient: Ingredient) -> Ingredient | None:
    """Fix LLM ingredients where quantity leaked into the name field."""
    name = (ingredient.name or "").strip()
    quantity = (ingredient.quantity or "").strip()
    unit = (ingredient.unit or "").strip()

    if not name:
        return None
    if re.fullmatch(r"[\d./\s\-–]+", name):
        return None

    if re.match(r"^\d", name):
        match = _NUMERIC_QTY_RE.match(name)
        if match:
            extra_qty = match.group(1).strip()
            remainder = match.group(2).strip()
            extra_unit = ""
            unit_match = re.match(
                rf"^(({_MEASUREMENT_UNITS})|c)\s+(.+)$",
                remainder,
                re.IGNORECASE,
            )
            if unit_match:
                extra_unit = unit_match.group(1).strip()
                remainder = unit_match.group(3).strip()
            if remainder and len(remainder) > 2:
                quantity = " ".join(part for part in [quantity, extra_qty] if part).strip() or extra_qty
                unit = unit or extra_unit
                name = remainder

    if len(name) < 2 or re.fullmatch(r"[\d./\s\-–]+", name):
        return None

    return Ingredient(
        name=name,
        quantity=quantity,
        unit=unit,
        notes=ingredient.notes,
        category=ingredient.category,
    )

CATEGORY_ALIASES: dict[str, GroceryCategory] = {
    "protein": GroceryCategory.MEAT,
    "poultry": GroceryCategory.MEAT,
    "seafood": GroceryCategory.MEAT,
    "fish": GroceryCategory.MEAT,
    "vegetable": GroceryCategory.PRODUCE,
    "vegetables": GroceryCategory.PRODUCE,
    "fruit": GroceryCategory.PRODUCE,
    "fruits": GroceryCategory.PRODUCE,
    "herb": GroceryCategory.PRODUCE,
    "herbs": GroceryCategory.PRODUCE,
    "grain": GroceryCategory.PANTRY,
    "grains": GroceryCategory.PANTRY,
    "starch": GroceryCategory.PANTRY,
    "legume": GroceryCategory.PANTRY,
    "legumes": GroceryCategory.PANTRY,
    "oil": GroceryCategory.PANTRY,
    "oils": GroceryCategory.PANTRY,
    "acid": GroceryCategory.PANTRY,
    "spice": GroceryCategory.SPICES,
    "spices": GroceryCategory.SPICES,
    "nutrient": GroceryCategory.OTHER,
    "misc": GroceryCategory.OTHER,
}


def normalize_grocery_category(value: str | GroceryCategory | None) -> GroceryCategory:
    if isinstance(value, GroceryCategory):
        return value
    if value is None:
        return GroceryCategory.OTHER
    text = str(value).lower().strip()
    if text in GroceryCategory._value2member_map_:
        return GroceryCategory(text)
    return CATEGORY_ALIASES.get(text, GroceryCategory.OTHER)


CANONICAL_NAMES: dict[str, str] = {
    "plain greek yogurt": "greek yogurt",
    "greek yogurt": "greek yogurt",
    "yogurt": "greek yogurt",
    "cheddar cheese slices": "cheddar cheese",
    "shredded cheese": "cheddar cheese",
    "cheddar cheese": "cheddar cheese",
    "cheese": "cheddar cheese",
    "feta cheese": "feta",
    "whole wheat tortilla": "tortillas",
    "whole wheat tortillas": "tortillas",
    "tortilla": "tortillas",
    "tortillas": "tortillas",
    "chicken thighs": "chicken thighs",
    "chicken breast": "chicken breast",
    "chicken breasts": "chicken breast",
    "ground turkey": "ground turkey",
    "turkey slices": "deli turkey",
    "turkey": "deli turkey",
    "canned tuna": "canned tuna",
    "canned white beans": "cannellini beans",
    "white beans": "cannellini beans",
    "black beans": "black beans",
    "chickpeas": "chickpeas",
    "rolled oats": "oats",
    "oats": "oats",
    "whole grain bread": "bread",
    "toast": "bread",
    "whole grain crackers": "crackers",
    "red grapes": "grapes",
    "fresh berries": "berries",
    "mixed berries": "berries",
    "berries": "berries",
    "bell pepper": "bell peppers",
    "peppers": "bell peppers",
    "cherry tomatoes": "cherry tomatoes",
    "fresh parsley": "parsley",
    "fresh oregano": "oregano",
    "lemon": "lemons",
    "lemon juice": "lemon juice",
    "english cucumber": "cucumber",
    "zucchini": "zucchini",
    "sweet potato": "sweet potatoes",
    "green beans": "green beans",
    "broccoli": "broccoli",
    "spinach": "spinach",
    "carrots": "carrots",
    "potatoes": "potatoes",
    "rice short grain pre cooked or uncooked": "rice",
    "rice": "rice",
    "pasta": "pasta",
    "salmon": "salmon",
    "beef sirloin or flank steak": "beef",
    "beef": "beef",
    "avocado": "avocados",
    "banana": "bananas",
    "bananas": "bananas",
    "egg": "eggs",
    "eggs": "eggs",
    "milk": "milk",
    "almond milk": "almond milk",
    "butter": "butter",
    "hummus": "hummus",
    "granola": "granola",
    "lentils": "lentils",
    "celery": "celery",
    "onions": "onions",
    "onion": "onions",
    "garlic": "garlic",
    "broth": "broth",
    "soy sauce": "soy sauce",
    "olive oil": "olive oil",
    "salt": "salt",
    "black pepper": "black pepper",
    "cinnamon": "cinnamon",
    "baking powder": "baking powder",
    "parmesan": "parmesan",
    "peas": "peas",
    "lettuce": "lettuce",
    "tomato soup": "tomato soup",
    "salsa": "salsa",
    "chia seeds": "chia seeds",
    "mini quesadillas": "tortillas",
}

SHOPPABLE_DEFAULTS: dict[str, str] = {
    "eggs": "1 dozen",
    "milk": "1 gallon",
    "almond milk": "1 carton",
    "greek yogurt": "1 large tub",
    "butter": "1 lb",
    "bread": "1 loaf",
    "tortillas": "1 pack",
    "cheddar cheese": "8 oz block",
    "feta": "4 oz",
    "deli turkey": "1/2 lb",
    "chicken thighs": "2 lb",
    "chicken breast": "1.5 lb",
    "chicken": "2 lb",
    "ground turkey": "1 lb",
    "beef": "1.5 lb",
    "salmon": "1.5 lb",
    "broccoli": "2 heads",
    "spinach": "1 bag",
    "lettuce": "1 head",
    "cucumber": "2",
    "bell peppers": "3",
    "carrots": "1 bag",
    "bananas": "1 bunch",
    "avocados": "3",
    "potatoes": "3 lb bag",
    "sweet potatoes": "3",
    "zucchini": "3",
    "green beans": "1 lb",
    "grapes": "1 bag",
    "berries": "2 pints",
    "oats": "1 container",
    "quinoa": "1 bag",
    "rice": "1 bag",
    "pasta": "1 box",
    "chickpeas": "2 cans",
    "black beans": "2 cans",
    "cannellini beans": "1 can",
    "canned tuna": "2 cans",
    "lentils": "1 bag",
    "broth": "1 carton",
    "crackers": "1 box",
    "granola": "1 bag",
    "peas": "1 bag frozen",
    "parmesan": "1 wedge",
    "lemons": "3",
    "onions": "1 bag",
    "garlic": "1 head",
    "celery": "1 bunch",
    "tomato soup": "2 cans",
    "hummus": "1 tub",
    "chia seeds": "1 small bag",
    "cherry tomatoes": "1 pint",
    "parsley": "1 bunch",
}


@dataclass
class ConsolidatedIngredient:
    canonical_name: str
    display_name: str
    category: GroceryCategory
    mention_count: int = 0
    raw_quantities: list[str] = field(default_factory=list)
    used_in_meals: list[str] = field(default_factory=list)


def canonicalize_name(name: str) -> str:
    key = _normalize_name(name)
    if key in CANONICAL_NAMES:
        return CANONICAL_NAMES[key]
    for alias, canonical in CANONICAL_NAMES.items():
        if alias in key or key in alias:
            return canonical
    return key


def _is_vague_quantity(quantity: str, unit: str) -> bool:
    combined = f"{quantity} {unit}".lower().strip()
    if not combined or combined in {"portion", "1 portion", "some", "to taste"}:
        return True
    vague = ("portion", "to taste", "as needed", "some")
    return any(token in combined for token in vague)


def _shop_quantity(canonical: str, mention_count: int, raw_quantities: list[str]) -> str:
    if canonical in SHOPPABLE_DEFAULTS:
        base = SHOPPABLE_DEFAULTS[canonical]
        if canonical == "eggs" and mention_count >= 8:
            return "18-count or 2 dozen"
        if mention_count >= 4 and "can" in base:
            count = min(4, 1 + mention_count // 3)
            return f"{count} cans"
        return base

    numeric_parts = []
    for raw in raw_quantities:
        match = re.search(r"(\d+(?:\.\d+)?)", raw)
        if match:
            numeric_parts.append(float(match.group(1)))

    if numeric_parts:
        total = sum(numeric_parts)
        if total.is_integer():
            return f"{int(total)}"
        return f"{total:g}"

    if mention_count > 1:
        return f"{mention_count} meals this week"
    return ""


class GroceryNormalizer:
    def __init__(self, pantry: PantryConfig | None = None) -> None:
        self.pantry = pantry or PantryConfig.from_settings()

    def consolidate_ingredients(self, ingredients: list[Ingredient]) -> list[ConsolidatedIngredient]:
        grouped: dict[str, ConsolidatedIngredient] = {}
        for raw in ingredients:
            ing = repair_ingredient(raw)
            if ing is None:
                continue
            canonical = canonicalize_name(ing.name)
            display = canonical.replace("_", " ").title()
            if canonical not in grouped:
                grouped[canonical] = ConsolidatedIngredient(
                    canonical_name=canonical,
                    display_name=display,
                    category=normalize_grocery_category(ing.category),
                )
            entry = grouped[canonical]
            entry.mention_count += 1
            qty = " ".join(part for part in [ing.quantity, ing.unit] if part).strip()
            if qty and qty not in entry.raw_quantities:
                entry.raw_quantities.append(qty)
        return list(grouped.values())

    def build_shopping_list(
        self,
        ingredients: list[Ingredient],
        week_label: str,
        *,
        weekly_plan_id: str | None = None,
        synergy_notes: str = "",
    ) -> GroceryList:
        consolidated = self.consolidate_ingredients(ingredients)
        must_buy: list[GroceryItem] = []
        weekly_staples: list[GroceryItem] = []
        pantry_using: list[str] = []

        for entry in sorted(consolidated, key=lambda item: item.display_name):
            if self.pantry.matches_on_hand(entry.canonical_name):
                pantry_using.append(entry.display_name)
                logger.debug("Pantry assumed: %s", entry.display_name)
                continue

            vague = all(_is_vague_quantity(q, "") for q in entry.raw_quantities) or not entry.raw_quantities
            shop_qty = _shop_quantity(entry.canonical_name, entry.mention_count, entry.raw_quantities)

            item = GroceryItem(
                name=entry.display_name,
                quantity=shop_qty if vague or _is_vague_quantity(entry.raw_quantities[0] if entry.raw_quantities else "", "") else shop_qty,
                category=entry.category,
                notes=f"Used in {entry.mention_count} meals this week" if entry.mention_count > 1 else "",
            )

            if self.pantry.matches_weekly_staple(entry.canonical_name):
                item.section = "weekly_staple"
                weekly_staples.append(item)
            else:
                item.section = "must_buy"
                must_buy.append(item)

        pantry_using.sort()
        return GroceryList(
            weekly_plan_id=weekly_plan_id,
            week_label=week_label,
            items=must_buy + weekly_staples,
            must_buy=must_buy,
            weekly_staples=weekly_staples,
            pantry_assumed=sorted(set(pantry_using)),
            synergy_notes=synergy_notes,
            ready_for_shopping=True,
        )

    def refine_llm_items(self, items: list[GroceryItem], synergy_notes: str = "") -> GroceryList:
        """Re-process LLM output through the same human-friendly pipeline."""
        ingredients = [
            Ingredient(
                name=item.name,
                quantity=item.quantity,
                unit=item.unit,
                category=item.category.value if hasattr(item.category, "value") else str(item.category),
            )
            for item in items
        ]
        return self.build_shopping_list(
            ingredients,
            week_label="",
            synergy_notes=synergy_notes,
        )
