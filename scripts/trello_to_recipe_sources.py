#!/usr/bin/env python3
"""Convert a Trello board JSON export into recipe_sources.yaml + markdown files.

Usage:
  python scripts/trello_to_recipe_sources.py \\
    ../ai-data/mealprepper/trello-export.json \\
    --recipes-dir ../ai-data/mealprepper/recipes \\
    --output config/recipe_sources.yaml
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


def slugify(title: str) -> str:
    cleaned = re.sub(r"^(Breakfast|Lunch|Dinner):\s*", "", title, flags=re.IGNORECASE)
    cleaned = cleaned.strip().lower()
    cleaned = re.sub(r"[^\w\s-]", "", cleaned)
    cleaned = re.sub(r"[-\s]+", "-", cleaned).strip("-")
    return cleaned[:80] or "recipe"


def infer_meal_block(title: str) -> str:
    lower = title.lower()
    if lower.startswith("breakfast:"):
        return "toddler_breakfast"
    if lower.startswith("lunch:"):
        return "toddler_school_lunch"
    return "adult_dinner"


def clean_title(title: str) -> str:
    return re.sub(r"^(Breakfast|Lunch|Dinner):\s*", "", title, flags=re.IGNORECASE).strip() or title.strip()


def attachment_urls(card: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for item in card.get("attachments") or []:
        url = str(item.get("url", "")).strip()
        if url:
            urls.append(url)
    return urls


def looks_like_recipe(desc: str) -> bool:
    if len(desc) < 120:
        return False
    lines = [line.strip() for line in desc.splitlines() if line.strip()]
    if len(lines) >= 4:
        return True
    cooking_markers = (
        "preheat",
        "oven",
        "bake",
        "cook",
        "chop",
        "mix",
        "season",
        "sauté",
        "saute",
        "grill",
        "simmer",
        "boil",
        "slice",
        "toss",
    )
    lowered = desc.lower()
    return sum(1 for marker in cooking_markers if marker in lowered) >= 2


def format_steps(desc: str) -> list[str]:
    lines = [line.strip() for line in desc.splitlines() if line.strip()]
    if not lines:
        return []
    if len(lines) == 1 and ". " in lines[0]:
        parts = [part.strip() for part in re.split(r"(?<=[.!?])\s+", lines[0]) if part.strip()]
        return parts or lines
    return lines


def write_markdown(path: Path, title: str, desc: str, meal_block: str, notes: str = "") -> None:
    display_title = clean_title(title)
    lines = [
        f"# {display_title}",
        f"blocks: {meal_block}",
        "tags: trello-import",
    ]
    if notes:
        lines.append(f"notes: {notes}")
    lines.extend(["", "## Steps"])
    for index, step in enumerate(format_steps(desc), start=1):
        lines.append(f"{index}. {step}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def yaml_quote(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def load_cards(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict) and isinstance(payload.get("cards"), list):
        return [item for item in payload["cards"] if isinstance(item, dict)]
    raise ValueError(f"Unsupported Trello export format: {path}")


@dataclass
class ConvertResult:
    sources: list[dict[str, Any]]
    markdown_writes: list[tuple[Path, str, str, str]]


def convert_cards(cards: list[dict[str, Any]], recipes_dir: Path) -> ConvertResult:
    sources: list[dict[str, Any]] = []
    markdown_writes: list[tuple[Path, str, str, str]] = []
    used_slugs: dict[str, int] = {}

    for card in cards:
        name = str(card.get("name", "")).strip()
        desc = str(card.get("desc", "")).strip()
        if not name and not desc:
            continue

        urls = attachment_urls(card)
        meal_block = infer_meal_block(name)
        display_title = clean_title(name) if name else "Untitled recipe"
        base_slug = slugify(name or display_title)
        count = used_slugs.get(base_slug, 0)
        used_slugs[base_slug] = count + 1
        slug = base_slug if count == 0 else f"{base_slug}-{count + 1}"

        if desc and looks_like_recipe(desc):
            rel_path = Path("recipes") / f"{slug}.md"
            sources.append(
                {
                    "type": "file",
                    "path": f"../ai-data/mealprepper/{rel_path.as_posix()}",
                    "label": display_title,
                }
            )
            markdown_writes.append((recipes_dir / rel_path.name, name, desc, meal_block))
            continue

        if urls:
            entry: dict[str, Any] = {
                "type": "url",
                "url": urls[0],
                "label": display_title,
            }
            note_parts: list[str] = []
            if desc:
                note_parts.append(desc)
            if len(urls) > 1:
                note_parts.append("Alternate links: " + ", ".join(urls[1:]))
            if note_parts:
                entry["notes"] = " ".join(note_parts)
            sources.append(entry)
            continue

        text = desc or display_title
        entry = {
            "type": "text",
            "title": display_title,
            "text": text,
            "label": f"Trello: {name}" if name else "Trello idea",
        }
        if meal_block:
            entry["meal_blocks"] = meal_block
        sources.append(entry)

    return ConvertResult(sources=sources, markdown_writes=markdown_writes)


def render_yaml(sources: list[dict[str, Any]]) -> str:
    lines = [
        "# Family recipe sources — sync with: mealprepper sync-recipes",
        "# Generated from Trello export via scripts/trello_to_recipe_sources.py",
        "#",
        "# Supported types:",
        "#   file          — markdown/text file",
        "#   url           — recipe page (fetched on sync)",
        "#   text          — inline meal idea (no URL or full recipe yet)",
        "",
        "sources:",
    ]
    for source in sources:
        lines.append(f"  - type: {source['type']}")
        for key, value in source.items():
            if key == "type":
                continue
            lines.append(f"    {key}: {yaml_quote(str(value))}")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trello_export", type=Path)
    parser.add_argument(
        "--recipes-dir",
        type=Path,
        default=Path("../ai-data/mealprepper/recipes"),
        help="Directory for generated markdown recipe files",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("config/recipe_sources.yaml"),
        help="Path to write recipe_sources.yaml (relative to MealPrepper root)",
    )
    parser.add_argument(
        "--mealprepper-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="MealPrepper project root",
    )
    args = parser.parse_args()

    export_path = args.trello_export
    if not export_path.is_absolute():
        export_path = args.mealprepper_root / export_path
    if not export_path.exists():
        print(f"Export not found: {export_path}", file=sys.stderr)
        return 1

    cards = load_cards(export_path)

    recipes_dir = args.recipes_dir
    if not recipes_dir.is_absolute():
        recipes_dir = args.mealprepper_root / recipes_dir
    recipes_dir.mkdir(parents=True, exist_ok=True)

    result = convert_cards(cards, recipes_dir)
    for path, name, desc, meal_block in result.markdown_writes:
        write_markdown(path, name, desc, meal_block)

    output_path = args.output
    if not output_path.is_absolute():
        output_path = args.mealprepper_root / output_path
    output_path.write_text(render_yaml(result.sources), encoding="utf-8")

    file_count = sum(1 for source in result.sources if source["type"] == "file")
    url_count = sum(1 for source in result.sources if source["type"] == "url")
    text_count = sum(1 for source in result.sources if source["type"] == "text")
    print(f"Wrote {output_path}")
    print(f"  {file_count} markdown files, {url_count} URLs, {text_count} text ideas ({len(result.sources)} total)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
