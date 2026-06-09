from __future__ import annotations

import hashlib
import json
import logging
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field, field_validator

from mealprepper.config import Settings, get_settings
from mealprepper.context.budget import CallType, load_context_budget
from mealprepper.context.prompt_builder import PromptBuilder
from mealprepper.index.recipe_index import RecipeIndex
from mealprepper.llm.ollama_client import OllamaClient, OllamaUnavailableError
from mealprepper.models.meals import Ingredient, MealRecipe, RecipeStep
from mealprepper.models.recipe_repository import SavedRecipe
from mealprepper.skills.meal_blocks import WeekMealOutline
from mealprepper.skills.pantry_config import _normalize_name
from mealprepper.skills.recipe_matching import MIN_RECIPE_MATCH_SCORE, recipe_match_score
from mealprepper.storage.sqlite import SQLiteStore

logger = logging.getLogger(__name__)

SOURCE_KEEP_RANK = {"file": 4, "url": 3, "text": 2, "trello": 1}


def _normalize_recipe_title(title: str) -> str:
    cleaned = re.sub(r"^(breakfast|lunch|dinner):\s*", "", title, flags=re.IGNORECASE)
    return _normalize_name(cleaned)


def _duplicate_keep_score(recipe: SavedRecipe) -> tuple[int, int, str]:
    updated = recipe.updated_at.isoformat() if recipe.updated_at else ""
    return (
        1 if recipe.has_full_recipe() else 0,
        SOURCE_KEEP_RANK.get(recipe.source_type, 0),
        updated,
    )


DEFAULT_FETCH_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def _parse_ingredient_line(line: str) -> Ingredient:
    cleaned = line.strip().lstrip("- ").strip()
    if "—" in cleaned:
        quantity, name = cleaned.split("—", 1)
        return Ingredient(name=name.strip(), quantity=quantity.strip())
    unit_match = re.search(
        r"\b(cups?|tbsp|tablespoons?|tsp|teaspoons?|oz|ounces?|lb|pounds?|g|grams?|ml|packages?|count)\b",
        cleaned,
        flags=re.IGNORECASE,
    )
    if unit_match:
        quantity = cleaned[: unit_match.end()].strip()
        name = cleaned[unit_match.end() :].strip().lstrip(",").strip()
        if name:
            return Ingredient(name=name, quantity=quantity)
    return Ingredient(name=cleaned)


class RecipeImportPayload(BaseModel):
    title: str
    description: str = ""
    meal_blocks: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    family_notes: str = ""
    key_ingredients: list[str] = Field(default_factory=list)
    prep_minutes: int = 0
    cook_minutes: int = 0
    servings: int = 4
    ingredients: list[Ingredient] = Field(default_factory=list)
    steps: list[RecipeStep] = Field(default_factory=list)
    infant_guidance: str = ""
    toddler_modifications: str = ""

    @field_validator("prep_minutes", "cook_minutes", "servings", mode="before")
    @classmethod
    def coerce_int_fields(cls, value: object) -> int:
        if value is None or value == "":
            return 0
        return int(value)

    @field_validator("ingredients", mode="before")
    @classmethod
    def normalize_ingredients(cls, value: object) -> list:
        if not value:
            return []
        if not isinstance(value, list):
            return value
        normalized = []
        for item in value:
            if isinstance(item, Ingredient):
                normalized.append(item)
            elif isinstance(item, dict):
                normalized.append(Ingredient.model_validate(item))
            elif isinstance(item, str):
                normalized.append(_parse_ingredient_line(item))
        return normalized

    @field_validator("steps", mode="before")
    @classmethod
    def normalize_steps(cls, value: object) -> list:
        if not value:
            return []
        if not isinstance(value, list):
            return value
        normalized = []
        for index, item in enumerate(value, start=1):
            if isinstance(item, RecipeStep):
                normalized.append(item)
            elif isinstance(item, dict):
                normalized.append(RecipeStep.model_validate(item))
            elif isinstance(item, str):
                normalized.append(RecipeStep(order=index, instruction=item))
        return normalized


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        text = data.strip()
        if text:
            self.parts.append(text)

    def text(self) -> str:
        return "\n".join(self.parts)


class RecipeRepositorySkill:
    """Import, store, and retrieve family recipe ideas and full recipes."""

    SYSTEM = """You extract structured recipe data for a family meal planner.
Return practical JSON. If the source is only a short idea (no full recipe), still return title,
family_notes, key_ingredients, and suggested meal_blocks — leave ingredients/steps empty."""

    def __init__(
        self,
        store: SQLiteStore | None = None,
        llm: OllamaClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.store = store or SQLiteStore(settings=self.settings)
        self.budget = load_context_budget(self.settings)
        self.llm = llm or OllamaClient(settings=self.settings, budget=self.budget)
        self.index = RecipeIndex(db_path=self.store.db_path, settings=self.settings)
        cfg = self.settings.merged_config().get("index", {})
        self.recipe_top_k = int(cfg.get("recipe_top_k", 6))

    def import_text(
        self,
        text: str,
        *,
        title: str | None = None,
        source_url: str = "",
        source_label: str = "",
        source_type: str = "text",
        notes: str = "",
        force: bool = False,
    ) -> SavedRecipe:
        cleaned = text.strip()
        if not cleaned:
            raise ValueError("Recipe text is empty")
        content_hash = self._content_hash(cleaned, source_url, source_label)
        if not force:
            existing = self.store.find_saved_recipe_by_hash(content_hash)
            if existing:
                logger.info("Recipe already imported: %s", existing.title)
                return existing

        parsed = self._parse_content(cleaned, title=title, source_url=source_url)
        saved = self._payload_to_saved(
            parsed,
            raw_text=cleaned,
            source_type=source_type,
            source_url=source_url,
            source_label=source_label,
            content_hash=content_hash,
            notes=notes or parsed.family_notes,
        )
        return self.store.save_saved_recipe(saved)

    def import_url(self, url: str, *, label: str = "") -> SavedRecipe:
        return self._import_url_source(url, label=label)

    def _import_url_source(self, url: str, *, label: str = "", notes: str = "") -> SavedRecipe:
        text, final_url = self._fetch_url_text(url)
        if notes.strip():
            text = f"{text.strip()}\n\nFamily notes:\n{notes.strip()}".strip()
        return self.import_text(
            text,
            title=label or None,
            source_url=final_url,
            source_label=label or final_url,
            source_type="url",
            notes=notes,
        )

    def import_file(self, path: Path | str, *, label: str = "", force: bool = False) -> SavedRecipe:
        file_path = Path(path)
        if not file_path.is_absolute():
            file_path = self.settings.project_root / file_path
        if not file_path.exists():
            raise FileNotFoundError(file_path)
        text = file_path.read_text(encoding="utf-8")
        return self.import_text(
            text,
            title=self._title_from_filename(file_path),
            source_url=file_path.as_uri(),
            source_label=label or file_path.name,
            source_type="file",
            force=force,
        )

    def import_trello_export(self, path: Path | str) -> list[SavedRecipe]:
        file_path = Path(path)
        if not file_path.is_absolute():
            file_path = self.settings.project_root / file_path
        payload = json.loads(file_path.read_text(encoding="utf-8"))
        cards = self._extract_trello_cards(payload)
        imported: list[SavedRecipe] = []
        for card in cards:
            name = str(card.get("name", "")).strip()
            desc = str(card.get("desc", "")).strip()
            if not name and not desc:
                continue
            card_url = str(card.get("url", card.get("shortUrl", ""))).strip()
            text = desc
            attachment_urls = [
                str(item.get("url", ""))
                for item in card.get("attachments", [])
                if item.get("url")
            ]
            if attachment_urls and not text:
                text = f"Reference: {attachment_urls[0]}"
            if attachment_urls:
                text = f"{text}\n\nReference links:\n" + "\n".join(attachment_urls)
            saved = self.import_text(
                text or name,
                title=name or None,
                source_url=card_url,
                source_label=f"Trello: {name}" if name else "Trello card",
                source_type="trello",
            )
            imported.append(saved)
        return imported

    def sync_sources(self) -> list[SavedRecipe]:
        raw = self.settings.load_yaml("recipe_sources.yaml")
        sources = raw.get("sources") or []
        imported: list[SavedRecipe] = []
        for source in sources:
            if not isinstance(source, dict):
                continue
            source_type = str(source.get("type", "file")).lower()
            try:
                if source_type == "url":
                    imported.append(
                        self._import_url_source(
                            str(source["url"]),
                            label=str(source.get("label", "")),
                            notes=str(source.get("notes", "")),
                        )
                    )
                elif source_type == "file":
                    imported.append(
                        self.import_file(
                            str(source["path"]),
                            label=str(source.get("label", "")),
                            force=bool(source.get("force", False)),
                        )
                    )
                elif source_type in {"trello", "trello_export"}:
                    imported.extend(self.import_trello_export(str(source["path"])))
                elif source_type == "text":
                    imported.append(
                        self.import_text(
                            str(source.get("text", "")),
                            title=str(source.get("title", "")) or None,
                            source_label=str(source.get("label", "Manual note")),
                            notes=str(source.get("notes", "")),
                        )
                    )
            except (ValueError, FileNotFoundError, httpx.HTTPError, json.JSONDecodeError) as exc:
                logger.warning("Failed to sync recipe source %s: %s", source, exc)
        return imported

    def find_recipes_by_query(self, query: str) -> list[SavedRecipe]:
        """Find saved recipes whose title matches a query (normalized substring)."""
        normalized_query = _normalize_recipe_title(query)
        if not normalized_query:
            return []

        matches: list[SavedRecipe] = []
        query_tokens = set(normalized_query.split())
        for recipe in self.store.list_saved_recipes(limit=0):
            normalized_title = _normalize_recipe_title(recipe.title)
            if normalized_query == normalized_title:
                matches.insert(0, recipe)
                continue
            if normalized_query in normalized_title or normalized_title in normalized_query:
                matches.append(recipe)
                continue
            if query_tokens & set(normalized_title.split()):
                matches.append(recipe)
        return matches

    def remove_recipe(self, query: str, *, dry_run: bool = False) -> SavedRecipe:
        """Delete a saved recipe by title query. Raises ValueError if none or ambiguous."""
        matches = self.find_recipes_by_query(query)
        if not matches:
            raise ValueError(f"No saved recipe matches “{query}”.")

        normalized_query = _normalize_recipe_title(query)
        exact = [recipe for recipe in matches if _normalize_recipe_title(recipe.title) == normalized_query]
        if len(exact) == 1:
            target = exact[0]
        elif len(matches) == 1:
            target = matches[0]
        else:
            titles = ", ".join(recipe.title for recipe in matches[:5])
            raise ValueError(
                f"“{query}” matches {len(matches)} recipes ({titles}). Use a more specific title."
            )

        if not dry_run:
            deleted = self.store.delete_saved_recipe(target.id or "")
            if not deleted:
                raise ValueError(f"Failed to delete recipe “{target.title}”.")
            logger.info("Removed saved recipe: %s (%s)", target.title, target.id)
        return target

    def purge_duplicates(self, *, dry_run: bool = False) -> list[tuple[SavedRecipe, SavedRecipe]]:
        """Remove duplicate recipes, keeping the richest copy in each title group."""
        recipes = self.store.list_saved_recipes(limit=0)
        groups: dict[str, list[SavedRecipe]] = {}
        for recipe in recipes:
            key = _normalize_recipe_title(recipe.title)
            groups.setdefault(key, []).append(recipe)

        removed: list[tuple[SavedRecipe, SavedRecipe]] = []
        for items in groups.values():
            if len(items) < 2:
                continue
            ranked = sorted(items, key=_duplicate_keep_score, reverse=True)
            keeper = ranked[0]
            for duplicate in ranked[1:]:
                removed.append((duplicate, keeper))
                if not dry_run:
                    self.store.delete_saved_recipe(duplicate.id or "")

        return removed

    def search(
        self,
        query: str,
        *,
        meal_block: str | None = None,
        top_k: int | None = None,
    ):
        resolved = self.recipe_top_k if top_k is None else top_k
        return self.index.search(query, meal_block=meal_block, top_k=resolved)

    def search_for_planning(self, query: str = "family favorites dinner lunch") -> list:
        return self.search(query, top_k=self.recipe_top_k)

    def format_for_outline(self, recipes: list) -> str:
        if not recipes:
            return "No saved family recipes yet."
        lines = [
            "Draw inspiration from these saved family recipes and ideas.",
            "Adapt titles and ingredients — do not copy verbatim every night.",
            "",
            self.index.format_for_prompt(recipes),
        ]
        return "\n".join(lines)

    def format_for_prompt(self, recipes: list) -> str:
        return self.index.format_for_prompt(recipes)

    def match_outline(self, outline: WeekMealOutline) -> SavedRecipe | None:
        candidates = self.store.list_saved_recipes(limit=200)
        target = _normalize_name(outline.title)
        for candidate in candidates:
            if _normalize_name(candidate.title) == target:
                if candidate.meal_blocks and outline.meal_block not in candidate.meal_blocks:
                    continue
                return candidate
        results = self.search(outline.title, meal_block=outline.meal_block, top_k=3)
        best_id = ""
        best_score = 0
        for result in results:
            score = recipe_match_score(outline.title, result.title)
            if score > best_score:
                best_score = score
                best_id = result.recipe_id
        if best_score < MIN_RECIPE_MATCH_SCORE or not best_id:
            return None
        return self.store.get_saved_recipe(best_id)

    def _parse_content(
        self,
        text: str,
        *,
        title: str | None = None,
        source_url: str = "",
    ) -> RecipeImportPayload:
        quick = self._parse_simple_markdown(text, fallback_title=title)
        if quick and (quick.ingredients or quick.steps):
            return quick
        try:
            builder = PromptBuilder(
                budget=self.budget,
                call_type=CallType.RECIPE_IMPORT,
                system=self.SYSTEM,
                task="Parse this family recipe or meal idea into structured JSON.",
            )
            if title:
                builder.add_section("Suggested title", title, priority=5)
            if source_url:
                builder.add_section("Source", source_url, priority=6)
            builder.add_section(
                "Fields",
                "title, description, meal_blocks (array), tags, family_notes, key_ingredients, "
                "prep_minutes, cook_minutes, servings, ingredients, steps, infant_guidance, toddler_modifications.",
                priority=10,
            )
            builder.add_section(
                "Content",
                self.budget.truncate(text, CallType.RECIPE_IMPORT, label="recipe"),
                priority=20,
            )
            return self.llm.chat_json(builder.build_messages(), RecipeImportPayload, call_type=CallType.RECIPE_IMPORT)
        except (OllamaUnavailableError, ValueError) as exc:
            logger.warning("Recipe parse LLM failed, using fallback parser: %s", exc)
            if quick:
                return quick
            return RecipeImportPayload(
                title=title or self._guess_title(text),
                family_notes=text[:500],
                key_ingredients=self._guess_ingredients(text),
            )

    def _parse_simple_markdown(
        self,
        text: str,
        *,
        fallback_title: str | None = None,
    ) -> RecipeImportPayload | None:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return None

        title = fallback_title or lines[0].lstrip("# ").strip()
        meal_blocks: list[str] = []
        tags: list[str] = []
        notes: list[str] = []
        ingredients: list[Ingredient] = []
        steps: list[RecipeStep] = []
        section = ""

        for line in lines[1:]:
            lower = line.lower().lstrip("#").strip()
            if lower.startswith("blocks:"):
                meal_blocks = [part.strip() for part in line.split(":", 1)[1].split(",") if part.strip()]
                continue
            if lower.startswith("tags:"):
                tags = [part.strip() for part in line.split(":", 1)[1].split(",") if part.strip()]
                continue
            if lower.startswith("notes:"):
                notes.append(line.split(":", 1)[1].strip())
                continue
            if lower in {"ingredients", "ingredients:", "**ingredients**"}:
                section = "ingredients"
                continue
            if lower in {"steps", "steps:", "instructions", "instructions:", "**steps**"}:
                section = "steps"
                continue
            if section == "ingredients":
                if line.startswith("- "):
                    name = line[2:].split("—", 1)[0].split("-", 1)[0].strip()
                    qty_parts = line[2:].split("—", 1)
                    quantity = qty_parts[1].strip() if len(qty_parts) > 1 else ""
                    ingredients.append(Ingredient(name=name, quantity=quantity))
                else:
                    ingredients.append(_parse_ingredient_line(line))
                continue
            if section == "steps":
                match = re.match(r"(\d+)\.\s*(.+)", line)
                if match:
                    steps.append(RecipeStep(order=int(match.group(1)), instruction=match.group(2)))
                elif line.startswith("- "):
                    steps.append(RecipeStep(order=len(steps) + 1, instruction=line[2:]))
                else:
                    steps.append(RecipeStep(order=len(steps) + 1, instruction=line))
                continue

        if not ingredients and not steps:
            return None

        return RecipeImportPayload(
            title=title,
            family_notes="\n".join(notes),
            meal_blocks=meal_blocks,
            tags=tags,
            ingredients=ingredients,
            steps=steps,
            key_ingredients=[ingredient.name for ingredient in ingredients[:8]],
        )

    def _payload_to_saved(
        self,
        payload: RecipeImportPayload,
        *,
        raw_text: str,
        source_type: str,
        source_url: str,
        source_label: str,
        content_hash: str,
        notes: str,
    ) -> SavedRecipe:
        recipe = None
        if payload.ingredients or payload.steps:
            recipe = MealRecipe(
                title=payload.title,
                description=payload.description,
                prep_minutes=payload.prep_minutes,
                cook_minutes=payload.cook_minutes,
                servings=payload.servings,
                ingredients=payload.ingredients,
                steps=payload.steps,
                tags=payload.tags,
                infant_guidance=payload.infant_guidance,
                toddler_modifications=payload.toddler_modifications,
            )
        return SavedRecipe(
            title=payload.title,
            source_type=source_type,
            source_url=source_url,
            source_label=source_label,
            content_hash=content_hash,
            raw_text=raw_text,
            recipe=recipe,
            key_ingredients=payload.key_ingredients,
            meal_blocks=payload.meal_blocks,
            tags=payload.tags,
            notes=notes or payload.family_notes,
        )

    def _fetch_url_text(self, url: str) -> tuple[str, str]:
        try:
            with httpx.Client(
                follow_redirects=True,
                timeout=30.0,
                headers=DEFAULT_FETCH_HEADERS,
            ) as client:
                response = client.get(url)
                response.raise_for_status()
                final_url = str(response.url)
                text = self._extract_text_from_response(response.text, response.headers.get("content-type", ""))
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            hint = (
                "Many recipe sites (including Allrecipes) block automated downloads. "
                "Copy the recipe into a markdown file or paste it with:\n"
                "  mealprepper import-recipe --text \"...\"\n"
                "  mealprepper import-recipe --file my-recipe.md"
            )
            raise ValueError(f"Could not fetch {url} (HTTP {status}). {hint}") from exc
        except httpx.HTTPError as exc:
            raise ValueError(f"Could not fetch {url}: {exc}") from exc

        cleaned = re.sub(r"\n{3,}", "\n\n", text).strip()
        if self._looks_like_blocked_page(cleaned):
            raise ValueError(
                f"{url} returned a bot-check page instead of recipe content. "
                "Copy the recipe manually and use --text or --file instead."
            )
        if len(cleaned) < 40:
            raise ValueError(
                f"Could not extract meaningful text from {url}. "
                "Try copying the recipe and using --text or --file."
            )
        return cleaned, final_url

    def _extract_text_from_response(self, body: str, content_type: str) -> str:
        if "html" in content_type or "<html" in body.lower():
            json_ld = self._extract_json_ld_recipe(body)
            if json_ld:
                return json_ld
            parser = _HTMLTextExtractor()
            parser.feed(body)
            return parser.text()
        return body

    def _extract_json_ld_recipe(self, html: str) -> str:
        """Pull Recipe structured data when sites embed schema.org JSON-LD."""
        for match in re.finditer(
            r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
            html,
            flags=re.IGNORECASE | re.DOTALL,
        ):
            raw = match.group(1).strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            recipe = self._find_recipe_node(payload)
            if not recipe:
                continue
            return self._format_json_ld_recipe(recipe)
        return ""

    def _find_recipe_node(self, payload: Any) -> dict[str, Any] | None:
        if isinstance(payload, list):
            for item in payload:
                found = self._find_recipe_node(item)
                if found:
                    return found
            return None
        if not isinstance(payload, dict):
            return None
        node_type = payload.get("@type", "")
        if isinstance(node_type, list):
            types = [str(item).lower() for item in node_type]
        else:
            types = [str(node_type).lower()]
        if any("recipe" in item for item in types):
            return payload
        graph = payload.get("@graph")
        if graph:
            return self._find_recipe_node(graph)
        return None

    def _format_json_ld_recipe(self, recipe: dict[str, Any]) -> str:
        lines = [str(recipe.get("name", "Imported recipe")).strip()]
        description = str(recipe.get("description", "")).strip()
        if description:
            lines.extend(["", description])
        ingredients = recipe.get("recipeIngredient") or recipe.get("ingredients") or []
        if ingredients:
            lines.extend(["", "Ingredients"])
            for item in ingredients:
                lines.append(f"- {item}")
        instructions = recipe.get("recipeInstructions") or recipe.get("instructions") or []
        if instructions:
            lines.extend(["", "Steps"])
            step_number = 1
            for item in instructions:
                if isinstance(item, dict):
                    text = str(item.get("text") or item.get("name") or "").strip()
                else:
                    text = str(item).strip()
                if text:
                    lines.append(f"{step_number}. {text}")
                    step_number += 1
        return "\n".join(lines).strip()

    @staticmethod
    def _looks_like_blocked_page(text: str) -> bool:
        lowered = text.lower()
        markers = (
            "verify you are human",
            "access denied",
            "bot detection",
            "enable javascript",
            "cloudflare",
            "captcha",
        )
        if any(marker in lowered for marker in markers):
            return True
        first_lines = "\n".join(text.splitlines()[:8]).lower()
        return first_lines.strip() == "simple page"

    @staticmethod
    def _extract_trello_cards(payload: Any) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            if isinstance(payload.get("cards"), list):
                return [item for item in payload["cards"] if isinstance(item, dict)]
            if isinstance(payload.get("actions"), list):
                cards = []
                for action in payload["actions"]:
                    data = action.get("data", {})
                    card = data.get("card")
                    if isinstance(card, dict):
                        cards.append(
                            {
                                "name": card.get("name", ""),
                                "desc": card.get("desc", ""),
                                "url": card.get("url", ""),
                            }
                        )
                return cards
        return []

    @staticmethod
    def _content_hash(text: str, source_url: str = "", source_label: str = "") -> str:
        digest = hashlib.sha256()
        digest.update(source_url.encode("utf-8"))
        digest.update(source_label.encode("utf-8"))
        digest.update(text.encode("utf-8"))
        return digest.hexdigest()[:16]

    @staticmethod
    def _guess_title(text: str) -> str:
        for line in text.splitlines():
            cleaned = line.strip().lstrip("# ").strip()
            if cleaned:
                return cleaned[:120]
        return "Untitled recipe"

    @staticmethod
    def _guess_ingredients(text: str) -> list[str]:
        ingredients: list[str] = []
        for line in text.splitlines():
            if line.strip().startswith("- "):
                ingredients.append(line.strip()[2:].split("—", 1)[0].strip())
        return ingredients[:10]

    @staticmethod
    def _title_from_filename(path: Path) -> str:
        return path.stem.replace("-", " ").replace("_", " ").title()
