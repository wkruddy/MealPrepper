import json
import tempfile
from pathlib import Path

import httpx

from mealprepper.index.recipe_index import RecipeIndex
from mealprepper.skills.recipe_repository import RecipeRepositorySkill
from mealprepper.storage.sqlite import SQLiteStore


SAMPLE_RECIPE = """# Lemon Herb Chicken
blocks: adult_dinner
tags: family-favorite

## Ingredients
- chicken thighs — 2 lb
- lemon — 1
- oregano — 1 tsp

## Steps
1. Season chicken and roast at 425F until done.
2. Squeeze lemon over top before serving.
"""


def test_import_text_without_llm_uses_markdown_parser():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        SQLiteStore(db_path=db_path)
        repo = RecipeRepositorySkill(store=SQLiteStore(db_path=db_path))
        saved = repo.import_text(SAMPLE_RECIPE, source_label="test")
        assert saved.title == "Lemon Herb Chicken"
        assert saved.has_full_recipe()
        assert any("chicken" in ing.name.lower() for ing in saved.recipe.ingredients)


def test_recipe_index_search():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        store = SQLiteStore(db_path=db_path)
        repo = RecipeRepositorySkill(store=store)
        repo.import_text(SAMPLE_RECIPE, source_label="test")

        index = RecipeIndex(db_path=db_path)
        results = index.search("lemon chicken", meal_block="adult_dinner", top_k=3)
        assert results
        assert any("Lemon" in result.title for result in results)


def test_import_trello_export():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        db_path = tmp_path / "test.db"
        SQLiteStore(db_path=db_path)
        export_path = tmp_path / "trello.json"
        export_path.write_text(
            json.dumps(
                [
                    {"name": "Turkey Meatballs", "desc": "Bake with marinara. Toddler loves them.", "url": "https://trello.test/1"},
                ]
            ),
            encoding="utf-8",
        )
        repo = RecipeRepositorySkill(store=SQLiteStore(db_path=db_path))
        imported = repo.import_trello_export(export_path)
        assert len(imported) == 1
        assert imported[0].title == "Turkey Meatballs"
        assert "toddler" in imported[0].notes.lower() or "toddler" in imported[0].raw_text.lower()


HAWAIIAN_ROLLS = """# Hawaiian Roll Cinnamon Rolls
blocks: adult_dinner
tags: family-favorite, batch-friendly,
notes: Very sweet, easy to eat everything much too quickly.

## Ingredients
2/3 cup brown sugar
2 teaspoons ground cinnamon
9 tablespoons butter, softened, divided

## Steps
Preheat the oven to 350 degrees F (180 degrees C).
Bake in the preheated oven for 20 minutes.
"""


def test_import_plain_line_markdown_without_bullets():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        repo = RecipeRepositorySkill(store=SQLiteStore(db_path=db_path))
        saved = repo.import_text(HAWAIIAN_ROLLS, source_label="test")
        assert saved.title == "Hawaiian Roll Cinnamon Rolls"
        assert saved.has_full_recipe()
        assert len(saved.recipe.ingredients) == 3
        assert len(saved.recipe.steps) == 2
        assert any("brown sugar" in ing.name.lower() for ing in saved.recipe.ingredients)


def test_dedupe_by_content_hash():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        store = SQLiteStore(db_path=db_path)
        repo = RecipeRepositorySkill(store=store)
        first = repo.import_text("Quick idea: pasta with peas", title="Pasta Peas", source_label="note")
        second = repo.import_text("Quick idea: pasta with peas", title="Pasta Peas", source_label="note")
        assert first.id == second.id


def test_purge_duplicates_keeps_richest_copy():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        store = SQLiteStore(db_path=db_path)
        repo = RecipeRepositorySkill(store=store)
        repo.import_text("Old Trello card", title="Dinner: Tilapia Tacos", source_type="trello", source_label="old")
        kept = repo.import_text("New YAML idea", title="Tilapia Tacos", source_type="text", source_label="new")

        removed = repo.purge_duplicates()
        assert len(removed) == 1
        assert removed[0][1].id == kept.id
        remaining = store.list_saved_recipes(limit=0)
        assert len(remaining) == 1
        assert remaining[0].id == kept.id


def test_extract_json_ld_recipe():
    repo = RecipeRepositorySkill(store=SQLiteStore(db_path=Path("/tmp/unused")))
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@type":"Recipe","name":"Test Cookies","recipeIngredient":["flour","sugar"],
     "recipeInstructions":[{"text":"Mix"},{"text":"Bake"}]}
    </script>
    </head></html>
    """
    text = repo._extract_json_ld_recipe(html)
    assert "Test Cookies" in text
    assert "flour" in text
    assert "Mix" in text


def test_fetch_url_maps_http_403_to_helpful_error():
    repo = RecipeRepositorySkill(store=SQLiteStore(db_path=Path("/tmp/unused")))

    class FakeResponse:
        status_code = 403
        request = None

        def raise_for_status(self):
            raise httpx.HTTPStatusError("blocked", request=None, response=self)

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url):
            return FakeResponse()

    import mealprepper.skills.recipe_repository as module

    original = module.httpx.Client
    module.httpx.Client = FakeClient
    try:
        try:
            repo._fetch_url_text("https://example.com/recipe")
            assert False, "expected ValueError"
        except ValueError as exc:
            assert "HTTP 403" in str(exc)
            assert "--text" in str(exc)
    finally:
        module.httpx.Client = original
