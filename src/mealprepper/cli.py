from __future__ import annotations

import logging
import re
from datetime import date
from typing import Optional

from pathlib import Path

import httpx
import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

from mealprepper.config import get_settings
from mealprepper.models.grocery import GroceryList
from mealprepper.orchestration.supervisor import MealPrepperSupervisor
from mealprepper.services.family_admin import FamilyAdminService
from mealprepper.storage.migrations import DEFAULT_FAMILY_ID
from mealprepper.services.family_resolver import FamilyContext, FamilyResolver
from mealprepper.skills.cook_efficiency import CookEfficiencySkill
from mealprepper.skills.food_shelf_life import FoodShelfLifeSkill
from mealprepper.skills.grocery_builder import GroceryBuilderSkill
from mealprepper.skills.playbook_renderer import PlaybookRendererSkill
from mealprepper.skills.recipe_repository import RecipeRepositorySkill
from mealprepper.storage.sqlite import SQLiteStore

app = typer.Typer(
    name="mealprepper",
    help="Family meal planning with local Ollama agents.",
    no_args_is_help=True,
)
console = Console()


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(levelname)s %(name)s: %(message)s")
    # httpx logs every request at INFO — hide unless debugging
    if not verbose:
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)


def _parse_date(value: Optional[str]) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


def _resolve_family_context(
    family_id: Optional[str] = None,
    family_slug: Optional[str] = None,
) -> FamilyContext:
    if family_id and family_slug:
        raise typer.BadParameter("Use only one of --family-id or --family-slug")
    resolver = FamilyResolver()
    if family_slug:
        return resolver.for_family_slug(family_slug)
    if family_id:
        return resolver.for_family_id(family_id)
    return resolver.default()


def _resolve_family_id(
    family_id: Optional[str] = None,
    family_slug: Optional[str] = None,
) -> str:
    return _resolve_family_context(family_id, family_slug).family_id


def _supervisor(
    family_id: Optional[str] = None,
    family_slug: Optional[str] = None,
) -> MealPrepperSupervisor:
    ctx = _resolve_family_context(family_id, family_slug)
    return MealPrepperSupervisor(
        store=SQLiteStore(family_id=ctx.family_id),
        family_context=ctx,
    )


def _grocery_markdown_path(grocery: GroceryList) -> Path:
    filename = f"grocery-{grocery.week_label.replace(' ', '')}.md"
    return get_settings().data_dir / filename


@app.command("init-db")
def init_db(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging"),
    family_id: Optional[str] = typer.Option(None, "--family-id"),
    family_slug: Optional[str] = typer.Option(None, "--family-slug"),
) -> None:
    """Initialize the SQLite database schema."""
    _setup_logging(verbose)
    settings = get_settings()
    fid = _resolve_family_id(family_id, family_slug)
    store = SQLiteStore(family_id=fid)
    ctx = FamilyResolver(db_path=store.db_path).for_family_id(fid)
    console.print(f"[green]Database ready:[/green] {settings.database_path}")
    console.print(f"Tables initialized at {store.db_path}")
    console.print(f"Family: {fid} ({len(ctx.profile.members)} members)")


@app.command("plan-week")
def plan_week(
    week_start: Optional[str] = typer.Option(
        None, "--week-start", help="Monday of target week (YYYY-MM-DD)"
    ),
    auto_approve: bool = typer.Option(
        False, "--auto-approve", help="Skip notification approval step (dev/testing)"
    ),
    family_id: Optional[str] = typer.Option(None, "--family-id"),
    family_slug: Optional[str] = typer.Option(None, "--family-slug"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Generate the weekly meal plan (Saturday workflow)."""
    _setup_logging(verbose)
    supervisor = _supervisor(family_id, family_slug)
    state = supervisor.plan_week(week_start=_parse_date(week_start), auto_approve=auto_approve)

    if state.last_error:
        console.print(f"[red]Error:[/red] {state.last_error}")
        raise typer.Exit(1)

    plan = state.plan
    if plan:
        console.print(f"[green]Plan created[/green] {plan.week_start} — {plan.week_end}")
        console.print(f"Status: {plan.status.value} | Meals: {len(plan.meals)}")
        if plan.id:
            console.print(f"Plan ID: {plan.id}")
    for msg in state.messages:
        console.print(f"  • {msg}")


@app.command("generate-grocery")
def generate_grocery(
    plan_id: Optional[str] = typer.Option(None, "--plan-id", help="Weekly plan ID"),
    family_id: Optional[str] = typer.Option(None, "--family-id"),
    family_slug: Optional[str] = typer.Option(None, "--family-slug"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Build grocery list from approved plan (Sunday workflow)."""
    _setup_logging(verbose)
    supervisor = _supervisor(family_id, family_slug)
    state = supervisor.generate_grocery(plan_id=plan_id)

    if state.last_error:
        console.print(f"[red]Error:[/red] {state.last_error}")
        raise typer.Exit(1)

    grocery = state.grocery
    if grocery:
        console.print(f"[green]Grocery list ready[/green] — {len(grocery.items)} items")
        out_dir = get_settings().data_dir
        md_path = _grocery_markdown_path(grocery)
        text = GroceryBuilderSkill().render_text(grocery)
        md_path.write_text(text, encoding="utf-8")
        console.print(f"Saved: {md_path}")
    for msg in state.messages:
        console.print(f"  • {msg}")


@app.command("show-grocery")
def show_grocery(
    plan_id: Optional[str] = typer.Option(None, "--plan-id", help="Weekly plan ID"),
    grocery_id: Optional[str] = typer.Option(None, "--grocery-id", help="Grocery list ID"),
    markdown: bool = typer.Option(
        False, "--markdown", "-m", help="Show full markdown checklist"
    ),
    family_id: Optional[str] = typer.Option(None, "--family-id"),
    family_slug: Optional[str] = typer.Option(None, "--family-slug"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Display the latest or specified grocery list."""
    _setup_logging(verbose)
    fid = _resolve_family_id(family_id, family_slug)
    store = SQLiteStore(family_id=fid)

    if grocery_id:
        grocery = store.get_grocery(grocery_id)
    elif plan_id:
        grocery = store.get_grocery_for_plan(plan_id)
    else:
        grocery = store.get_latest_grocery()

    if not grocery:
        console.print("[yellow]No grocery list found. Run generate-grocery first.[/yellow]")
        raise typer.Exit(1)

    builder = GroceryBuilderSkill()
    md_path = _grocery_markdown_path(grocery)

    if markdown:
        console.print(Markdown(builder.render_text(grocery)))
    else:
        def _print_section(title: str, items: list) -> None:
            if not items:
                return
            table = Table(title=title)
            table.add_column("Item")
            table.add_column("Buy")
            table.add_column("Notes")
            for item in items:
                qty = item.quantity
                if item.unit:
                    qty = f"{qty} {item.unit}".strip()
                table.add_row(item.name, qty, item.notes or "")
            console.print(table)

        must_buy = grocery.must_buy or [i for i in grocery.items if i.section == "must_buy"]
        staples = grocery.weekly_staples or [i for i in grocery.items if i.section == "weekly_staple"]
        _print_section(f"Shop for recipes — {grocery.week_label}", must_buy)
        _print_section("Weekly staples (if low)", staples)
        if grocery.pantry_assumed:
            console.print(f"[dim]Pantry assumed:[/dim] {', '.join(grocery.pantry_assumed)}")

    console.print(f"Items: {len(grocery.items)} | ID: {grocery.id}")
    if md_path.exists():
        console.print(f"Markdown file: {md_path}")
    elif markdown:
        console.print(f"[dim]No saved file at {md_path}[/dim]")


@app.command("send-daily")
def send_daily(
    target_date: Optional[str] = typer.Option(
        None, "--date", help="Date for daily summary (YYYY-MM-DD, default today)"
    ),
    family_id: Optional[str] = typer.Option(None, "--family-id"),
    family_slug: Optional[str] = typer.Option(None, "--family-slug"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Send morning notification with today's meals."""
    _setup_logging(verbose)
    supervisor = _supervisor(family_id, family_slug)
    state = supervisor.send_daily(target=_parse_date(target_date))

    if state.last_error:
        console.print(f"[yellow]Warning:[/yellow] {state.last_error}")
    for msg in state.messages:
        console.print(f"  • {msg}")


@app.command("process-feedback")
def process_feedback(
    message: Optional[str] = typer.Option(
        None, "--message", "-m", help="Simulate inbound approval/feedback message"
    ),
    family_id: Optional[str] = typer.Option(None, "--family-id"),
    family_slug: Optional[str] = typer.Option(None, "--family-slug"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Process pending feedback into preferences, or parse a message."""
    _setup_logging(verbose)
    supervisor = _supervisor(family_id, family_slug)

    if message:
        state = supervisor.handle_message(message)
    else:
        state = supervisor.process_feedback()

    for msg in state.messages:
        console.print(f"  • {msg}")


def _load_plan(plan_id: Optional[str], family_id: str = DEFAULT_FAMILY_ID):
    store = SQLiteStore(family_id=family_id)
    plan = store.get_weekly_plan(plan_id) if plan_id else store.get_latest_plan()
    if not plan:
        console.print("[yellow]No plan found. Run plan-week first.[/yellow]")
        raise typer.Exit(1)
    return plan


def _print_synergy_report(plan, markdown: bool = False) -> None:
    cook_text = CookEfficiencySkill().render_report(plan)
    shelf_text = FoodShelfLifeSkill().render_audit(plan)
    combined = f"{cook_text.rstrip()}\n\n{shelf_text}"
    console.print(Markdown(combined))


@app.command("show-shelf-life")
def show_shelf_life(
    plan_id: Optional[str] = typer.Option(None, "--plan-id"),
    markdown: bool = typer.Option(False, "--markdown", "-m", help="Render as markdown"),
    family_id: Optional[str] = typer.Option(None, "--family-id"),
    family_slug: Optional[str] = typer.Option(None, "--family-slug"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Show leftover timing rules and any shelf-life issues for a plan."""
    _setup_logging(verbose)
    plan = _load_plan(plan_id, family_id=_resolve_family_id(family_id, family_slug))
    text = FoodShelfLifeSkill().render_audit(plan)
    console.print(Markdown(text))
    console.print(f"Plan ID: {plan.id}")


@app.command("show-synergy")
def show_synergy(
    plan_id: Optional[str] = typer.Option(None, "--plan-id"),
    markdown: bool = typer.Option(False, "--markdown", "-m", help="Render as markdown"),
    family_id: Optional[str] = typer.Option(None, "--family-id"),
    family_slug: Optional[str] = typer.Option(None, "--family-slug"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Show cook reuse links, shared ingredients, and synergy notes for a plan."""
    _setup_logging(verbose)
    plan = _load_plan(plan_id, family_id=_resolve_family_id(family_id, family_slug))
    _print_synergy_report(plan, markdown=markdown)
    console.print(f"Plan ID: {plan.id}")


@app.command("show-plan")
def show_plan(
    plan_id: Optional[str] = typer.Option(None, "--plan-id"),
    markdown: bool = typer.Option(False, "--markdown", "-m", help="Show full playbook markdown"),
    titles_only: bool = typer.Option(
        False,
        "--titles-only",
        "-t",
        help="Show only meal names grouped by day (no ingredients or prep details)",
    ),
    synergy: bool = typer.Option(
        False,
        "--synergy",
        "-s",
        help="Include cook-efficiency and ingredient synergy report",
    ),
    recipes: bool = typer.Option(
        False,
        "--recipes",
        "-r",
        help="Show full recipes with ingredients and step-by-step instructions",
    ),
    family_id: Optional[str] = typer.Option(None, "--family-id"),
    family_slug: Optional[str] = typer.Option(None, "--family-slug"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Display the latest or specified weekly plan."""
    _setup_logging(verbose)
    plan = _load_plan(plan_id, family_id=_resolve_family_id(family_id, family_slug))

    renderer = PlaybookRendererSkill()

    if recipes:
        text = renderer.render_full_recipes(plan)
        console.print(Markdown(text))
        console.print(f"Status: {plan.status.value} | Meals: {len(plan.meals)} | ID: {plan.id}")
        if synergy:
            console.print("")
            _print_synergy_report(plan)
        return

    if titles_only:
        text = renderer.render_titles_only(plan)
        if markdown:
            console.print(Markdown(text))
        else:
            for line in text.splitlines():
                if line.startswith("## "):
                    console.print(f"\n[bold cyan]{line[3:]}[/bold cyan]")
                elif line.startswith("# "):
                    console.print(f"[bold]{line[2:]}[/bold]")
                elif line.startswith("- "):
                    match = re.match(r"\*\*(.+?):\*\* (.+)", line[2:])
                    if match:
                        title_line = match.group(2)
                        note_match = re.match(r"(.+?) _\((.+)\)_$", title_line)
                        if note_match:
                            console.print(
                                f"  [dim]{match.group(1)}:[/dim] {note_match.group(1)} "
                                f"[italic dim]({note_match.group(2)})[/italic dim]"
                            )
                        else:
                            console.print(f"  [dim]{match.group(1)}:[/dim] {title_line}")
                    else:
                        console.print(f"  {line[2:]}")
                elif line.strip():
                    console.print(line)
        console.print(f"Status: {plan.status.value} | Meals: {len(plan.meals)} | ID: {plan.id}")
        if synergy:
            console.print("")
            _print_synergy_report(plan)
        return

    if markdown and plan.playbook_markdown and not synergy:
        console.print(Markdown(plan.playbook_markdown))
        return

    if markdown and plan.playbook_markdown:
        console.print(Markdown(plan.playbook_markdown))
        console.print("")
        _print_synergy_report(plan, markdown=True)
        console.print(f"Status: {plan.status.value} | ID: {plan.id}")
        return

    table = Table(title=f"Week {plan.week_start} — {plan.week_end}")
    table.add_column("Day")
    table.add_column("Block")
    table.add_column("Meal")
    table.add_column("Notes")
    for meal in plan.meals:
        r = meal.recipe
        note = meal.cook_note or ""
        if meal.cook_source_day:
            note = note or f"From {meal.cook_source_day} {meal.cook_source_block.replace('_', ' ')}"
        table.add_row(
            meal.day.title(),
            meal.meal_block.replace("_", " "),
            r.title,
            note,
        )
    console.print(table)
    console.print(f"Status: {plan.status.value} | ID: {plan.id}")
    if synergy:
        console.print("")
        _print_synergy_report(plan)


@app.command("import-recipe")
def import_recipe(
    text: Optional[str] = typer.Option(None, "--text", help="Recipe text or meal idea"),
    title: Optional[str] = typer.Option(None, "--title", help="Recipe title"),
    url: Optional[str] = typer.Option(None, "--url", help="Recipe page URL"),
    file: Optional[str] = typer.Option(None, "--file", "-f", help="Path to recipe markdown/text file"),
    trello_export: Optional[str] = typer.Option(None, "--trello-export", help="Trello board JSON export"),
    label: Optional[str] = typer.Option(None, "--label", help="Source label for display"),
    notes: Optional[str] = typer.Option(None, "--notes", help="Family notes about this recipe"),
    force: bool = typer.Option(False, "--force", help="Re-import even if this content was imported before"),
    family_id: Optional[str] = typer.Option(None, "--family-id"),
    family_slug: Optional[str] = typer.Option(None, "--family-slug"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Import a family recipe or meal idea into the searchable recipe library."""
    _setup_logging(verbose)
    fid = _resolve_family_id(family_id, family_slug)
    repo = RecipeRepositorySkill(store=SQLiteStore(family_id=fid))
    try:
        if trello_export:
            imported = repo.import_trello_export(trello_export)
            for item in imported:
                console.print(f"[green]Imported[/green] {item.title} ({item.id})")
            console.print(f"Imported {len(imported)} Trello cards")
            return
        if url:
            saved = repo.import_url(url, label=label or "")
        elif file:
            saved = repo.import_file(file, label=label or "", force=force)
        elif text:
            saved = repo.import_text(
                text,
                title=title,
                source_label=label or "Manual import",
                notes=notes or "",
                force=force,
            )
        else:
            console.print("[yellow]Provide --text, --url, --file, or --trello-export[/yellow]")
            raise typer.Exit(1)
    except (ValueError, FileNotFoundError, httpx.HTTPError) as exc:
        console.print(f"[red]Import failed:[/red] {exc}")
        raise typer.Exit(1)

    kind = "full recipe" if saved.has_full_recipe() else "meal idea"
    console.print(f"[green]Saved[/green] {saved.title} ({kind})")
    console.print(f"ID: {saved.id}")
    if saved.meal_blocks:
        console.print(f"Blocks: {', '.join(saved.meal_blocks)}")
    if saved.notes:
        console.print(f"Notes: {saved.notes[:200]}")


@app.command("list-recipes")
def list_recipes(
    query: Optional[str] = typer.Option(None, "--query", "-q", help="Search saved recipes"),
    limit: int = typer.Option(100, "--limit", "-n"),
    family_id: Optional[str] = typer.Option(None, "--family-id"),
    family_slug: Optional[str] = typer.Option(None, "--family-slug"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """List or search the family recipe library."""
    _setup_logging(verbose)
    fid = _resolve_family_id(family_id, family_slug)
    repo = RecipeRepositorySkill(store=SQLiteStore(family_id=fid))
    if query:
        matches = repo.search(query, top_k=limit)
        if not matches:
            console.print("[yellow]No matching recipes found.[/yellow]")
            return
        table = Table(title=f"Recipes matching '{query}'")
        table.add_column("Title")
        table.add_column("Source")
        table.add_column("Blocks")
        for match in matches:
            table.add_row(match.title, match.source_label or match.source_type, match.meal_blocks)
        console.print(table)
        return

    saved = repo.store.list_saved_recipes(limit=limit)
    if not saved:
        console.print("[yellow]No recipes saved yet. Try import-recipe or sync-recipes.[/yellow]")
        return
    table = Table(title="Family recipe library")
    table.add_column("Title")
    table.add_column("Type")
    table.add_column("Source")
    table.add_column("ID")
    for item in saved:
        kind = "recipe" if item.has_full_recipe() else "idea"
        table.add_row(item.title, kind, item.source_label or item.source_type, item.id or "")
    console.print(table)


@app.command("sync-recipes")
def sync_recipes(
    family_id: Optional[str] = typer.Option(None, "--family-id"),
    family_slug: Optional[str] = typer.Option(None, "--family-slug"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Import recipes from config/recipe_sources.yaml (files, URLs, Trello export)."""
    _setup_logging(verbose)
    fid = _resolve_family_id(family_id, family_slug)
    repo = RecipeRepositorySkill(store=SQLiteStore(family_id=fid))
    imported = repo.sync_sources()
    if not imported:
        console.print("[yellow]No sources imported. Edit config/recipe_sources.yaml[/yellow]")
        return
    for item in imported:
        kind = "recipe" if item.has_full_recipe() else "idea"
        console.print(f"  • {item.title} ({kind})")
    console.print(f"[green]Synced {len(imported)} source(s)[/green]")


@app.command("remove-recipe")
def remove_recipe(
    title: str = typer.Argument(..., help="Recipe title or distinctive substring to delete."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show the match without deleting."),
    family_id: Optional[str] = typer.Option(None, "--family-id"),
    family_slug: Optional[str] = typer.Option(None, "--family-slug"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Remove a single saved recipe from the family library."""
    _setup_logging(verbose)
    fid = _resolve_family_id(family_id, family_slug)
    repo = RecipeRepositorySkill(store=SQLiteStore(family_id=fid))
    try:
        removed = repo.remove_recipe(title, dry_run=dry_run)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    kind = "recipe" if removed.has_full_recipe() else "idea"
    action = "Would remove" if dry_run else "Removed"
    console.print(f"[green]{action}[/green] {removed.title} ({kind}, {removed.source_type})")


@app.command("purge-recipes")
def purge_recipes(
    duplicates: bool = typer.Option(
        False,
        "--duplicates",
        help="Remove duplicate recipes (keeps the best copy per normalized title).",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be removed without deleting."),
    family_id: Optional[str] = typer.Option(None, "--family-id"),
    family_slug: Optional[str] = typer.Option(None, "--family-slug"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Remove duplicate saved recipes."""
    _setup_logging(verbose)
    if not duplicates:
        console.print(
            "[yellow]Specify --duplicates to remove duplicate recipes, "
            "or use remove-recipe <title> for a single entry.[/yellow]"
        )
        raise typer.Exit(1)

    fid = _resolve_family_id(family_id, family_slug)
    repo = RecipeRepositorySkill(store=SQLiteStore(family_id=fid))
    removed = repo.purge_duplicates(dry_run=dry_run)
    if not removed:
        console.print("[green]No duplicate recipes found.[/green]")
        return

    for duplicate, keeper in removed:
        console.print(
            f"  • [red]remove[/red] {duplicate.title} ({duplicate.source_type}) "
            f"→ keep {keeper.title} ({keeper.source_type})"
        )
    action = "Would remove" if dry_run else "Removed"
    console.print(f"[green]{action} {len(removed)} duplicate recipe(s)[/green]")


@app.command("approve-plan")
def approve_plan(
    plan_id: Optional[str] = typer.Option(None, "--plan-id"),
    family_id: Optional[str] = typer.Option(None, "--family-id"),
    family_slug: Optional[str] = typer.Option(None, "--family-slug"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Manually approve a pending weekly plan."""
    _setup_logging(verbose)
    supervisor = _supervisor(family_id, family_slug)
    state = supervisor.approve_plan(plan_id=plan_id)
    if state.last_error:
        console.print(f"[red]{state.last_error}[/red]")
        raise typer.Exit(1)
    for msg in state.messages:
        console.print(f"  • {msg}")


family_app = typer.Typer(help="Manage families and Slack workspace bindings.")
app.add_typer(family_app, name="family")


def _format_family_slack_column(row) -> str:
    if row.slack_workspace_id:
        return f"{row.slack_workspace_id} (shared)"
    if row.slack_user_links:
        workspaces = sorted({link.split(":", 1)[0] for link in row.slack_user_links if ":" in link})
        if len(workspaces) == 1 and len(row.slack_user_links) == 1:
            return row.slack_user_links[0]
        if len(workspaces) == 1:
            return f"{workspaces[0]} ({len(row.slack_user_links)} users)"
        return ", ".join(row.slack_user_links)
    return "—"


@family_app.command("list")
def family_list(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Include id, created, timezone"),
) -> None:
    """List registered families, Slack bindings, and per-user households."""
    _setup_logging(verbose)
    admin = FamilyAdminService()
    families = admin.list_families()
    if not families:
        console.print("[yellow]No families found.[/yellow] Users can say `start` in Slack, or run `family add-slack-binding`.")
        return

    table = Table(title="Families")
    if verbose:
        table.add_column("ID")
        table.add_column("Created")
        table.add_column("Timezone")
    table.add_column("Slug")
    table.add_column("Name")
    table.add_column("Members")
    table.add_column("Recipes")
    table.add_column("Plans")
    table.add_column("Slack")
    table.add_column("Status")
    for row in families:
        cells = []
        if verbose:
            cells.extend([row.id, row.created_at[:10] if row.created_at else "—", row.timezone])
        cells.extend([
            row.slug,
            row.name,
            str(row.member_count),
            str(row.recipe_count),
            str(row.plan_count),
            _format_family_slack_column(row),
            row.status,
        ])
        table.add_row(*cells)
    console.print(table)

    households = admin.list_slack_user_households()
    if households:
        console.print(
            f"\n[dim]{len(households)} per-user household(s). "
            "Run `mealprepper family list-users` for the Slack user mapping.[/dim]"
        )


@family_app.command("list-users")
def family_list_users(
    workspace_id: str = typer.Option("", "--workspace-id", help="Filter to one Slack workspace"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """List Slack user → household mappings (debug multi-tenant onboarding)."""
    _setup_logging(verbose)
    admin = FamilyAdminService()
    households = admin.list_slack_user_households(workspace_id=workspace_id)
    if not households:
        if workspace_id.strip():
            console.print(f"[yellow]No per-user households in workspace {workspace_id}.[/yellow]")
        else:
            console.print("[yellow]No per-user households yet.[/yellow] Users create them with `start` in Slack.")
        return

    table = Table(title="Slack user households")
    table.add_column("Workspace")
    table.add_column("Slack user")
    table.add_column("Family slug")
    table.add_column("Family name")
    if verbose:
        table.add_column("Family id")
        table.add_column("Created")
    for row in households:
        cells = [
            row.workspace_id,
            row.slack_user_id,
            row.family_slug,
            row.family_name,
        ]
        if verbose:
            cells.extend([row.family_id, row.created_at[:19] if row.created_at else "—"])
        table.add_row(*cells)
    console.print(table)


@family_app.command("show")
def family_show(
    slug: str = typer.Argument(..., help="Family slug or id"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Show one family's members, diet, and Slack bindings."""
    _setup_logging(verbose)
    admin = FamilyAdminService()
    try:
        detail = admin.get_family_detail(slug)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    console.print(f"[bold]{detail.name}[/bold] ({detail.slug}) — {detail.status}")
    console.print(f"ID: {detail.id}")
    console.print(f"Timezone: {detail.timezone}")
    if detail.created_at:
        console.print(f"Created: {detail.created_at[:19]}")
    console.print(
        f"Library: {detail.recipe_count} recipe(s), {detail.plan_count} weekly plan(s), "
        f"{detail.member_count} member(s)"
    )
    if detail.dietary_household:
        console.print(f"Household diet: {', '.join(detail.dietary_household)}")

    if detail.slack_users:
        user_table = Table(title="Slack users")
        user_table.add_column("Workspace")
        user_table.add_column("User")
        user_table.add_column("Linked")
        for row in detail.slack_users:
            linked = row.get("created_at", "")[:19] if row.get("created_at") else "—"
            user_table.add_row(row["workspace_id"], row["slack_user_id"], linked)
        console.print(user_table)

    if detail.members:
        member_table = Table(title="Members")
        member_table.add_column("Name")
        member_table.add_column("Role")
        member_table.add_column("Age")
        for member in detail.members:
            age = ""
            if member.get("age_months") is not None:
                age = f"{member['age_months']}mo"
            elif member.get("age_years") is not None:
                age = f"{member['age_years']}y"
            member_table.add_row(member["display_name"], member["role"], age)
        console.print(member_table)
    else:
        console.print("[dim]No members yet — add via Slack `add member` or import YAML.[/dim]")

    if detail.slack_bindings:
        bind_table = Table(title="Slack bindings")
        bind_table.add_column("Workspace")
        bind_table.add_column("Channel")
        bind_table.add_column("Webhook")
        bind_table.add_column("Bot token")
        for binding in detail.slack_bindings:
            bind_table.add_row(
                binding["workspace_id"],
                binding["channel_id"],
                "set" if binding["webhook_url"] else "—",
                "set" if binding["bot_token_set"] else "env default",
            )
        console.print(bind_table)
    else:
        console.print("[dim]No Slack binding — use family add-slack-binding.[/dim]")


def _print_family_delete_result(result, *, dry_run: bool) -> None:
    action = "Would delete" if dry_run else "Deleted"
    console.print(f"[bold]{action}[/bold] household *{result.name}* (`{result.slug}`)")
    if result.deleted_rows:
        table = Table(title="Removed rows" if not dry_run else "Rows that would be removed")
        table.add_column("Table")
        table.add_column("Rows", justify="right")
        for name, count in sorted(result.deleted_rows.items()):
            if count:
                table.add_row(name, str(count))
        console.print(table)
    if result.slack_users_cleared:
        console.print(f"Slack user links cleared: {result.slack_users_cleared}")
    if result.slack_bindings_cleared:
        console.print(
            f"Shared workspace bindings unlinked: {result.slack_bindings_cleared} "
            "[dim](workspace stays installed; user can `start` again)[/dim]"
        )


@family_app.command("remove")
def family_remove(
    slug: str = typer.Argument(..., help="Family slug or id to delete"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm deletion"),
    force: bool = typer.Option(
        False,
        "--force",
        help=f"Allow deleting the `{DEFAULT_FAMILY_ID}` household",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Show what would be deleted without making changes",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Delete a household and all plans, recipes, and settings for it."""
    _setup_logging(verbose)
    admin = FamilyAdminService()
    try:
        if dry_run:
            result = admin.delete_family(slug, dry_run=True, force_default=force)
            _print_family_delete_result(result, dry_run=True)
            return

        if not yes:
            preview = admin.delete_family(slug, dry_run=True, force_default=force)
            _print_family_delete_result(preview, dry_run=True)
            console.print("\n[yellow]Re-run with --yes to delete.[/yellow]")
            raise typer.Exit(1)

        result = admin.delete_family(slug, force_default=force)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    _print_family_delete_result(result, dry_run=False)
    console.print("[green]Household removed.[/green]")


@family_app.command("add-slack-binding")
def family_add_slack_binding(
    slug: str = typer.Option(..., "--slug", help="Family slug (also used as id for new families)"),
    name: str = typer.Option(..., "--name", help="Display name for the family"),
    workspace_id: str = typer.Option(..., "--workspace-id", help="Slack team_id (workspace)"),
    channel_id: str = typer.Option(..., "--channel-id", help="Default Slack channel id"),
    webhook_url: str = typer.Option("", "--webhook-url", help="Optional incoming webhook URL"),
    bot_token: str = typer.Option(
        "",
        "--bot-token",
        help="Per-workspace Bot User OAuth token (xoxb-...); falls back to SLACK_BOT_TOKEN",
    ),
    timezone: str = typer.Option("America/New_York", "--timezone", help="Family timezone"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Create or update a family and bind it to a Slack workspace."""
    _setup_logging(verbose)
    admin = FamilyAdminService()
    try:
        detail = admin.add_slack_binding(
            slug=slug,
            name=name,
            workspace_id=workspace_id,
            channel_id=channel_id,
            webhook_url=webhook_url,
            bot_token=bot_token,
            timezone=timezone,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    console.print(f"[green]Slack binding saved[/green] for family `{detail.slug}`")
    console.print(f"  Workspace: {workspace_id}")
    console.print(f"  Channel: {channel_id}")
    if bot_token:
        console.print("  Bot token: stored in slack_bindings")
    else:
        console.print("  Bot token: will use SLACK_BOT_TOKEN from .env")


slack_app = typer.Typer(help="Slack app install and OAuth helpers.")
app.add_typer(slack_app, name="slack")


@slack_app.command("oauth-server")
def slack_oauth_server(
    port: int = typer.Option(8787, "--port", "-p", help="Local port for OAuth callback"),
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address (use 0.0.0.0 behind ngrok)"),
    redirect_uri: str = typer.Option(
        "",
        "--redirect-uri",
        help="Must match api.slack.com (default: SLACK_OAUTH_REDIRECT_URI from .env, then http://127.0.0.1:PORT/...)",
    ),
    family_slug: str = typer.Option(
        "",
        "--family-slug",
        help="If set, store bot token in slack_bindings for this family",
    ),
    family_name: str = typer.Option("", "--family-name", help="Display name when creating a new family"),
    channel_id: str = typer.Option("", "--channel-id", help="Slack channel id for the binding"),
    webhook_url: str = typer.Option("", "--webhook-url", help="Optional incoming webhook URL"),
    print_authorize_url: bool = typer.Option(
        True,
        "--print-authorize-url/--no-print-authorize-url",
        help="Print the OAuth authorize URL before listening",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Run a one-shot OAuth callback server to install the app in another workspace."""
    _setup_logging(verbose)
    from mealprepper.skills.comms.slack_oauth import (
        DEFAULT_CALLBACK_PATH,
        build_authorize_url,
        build_redirect_uri,
        format_oauth_result,
        run_oauth_server,
        slack_redirect_insecure_message,
    )

    settings = get_settings()
    client_id = settings.slack_client_id.strip()
    client_secret = settings.slack_client_secret.strip()
    if not client_id or not client_secret:
        console.print(
            "[red]Set SLACK_CLIENT_ID and SLACK_CLIENT_SECRET in .env[/red]\n"
            "Find them under api.slack.com → your app → Basic Information."
        )
        raise typer.Exit(1)

    redirect_host = "127.0.0.1" if host.strip() in {"0.0.0.0", "::"} else host
    resolved_redirect = (
        redirect_uri.strip()
        or settings.slack_oauth_redirect_uri.strip()
        or build_redirect_uri(redirect_host, port, DEFAULT_CALLBACK_PATH)
    )
    if insecure_msg := slack_redirect_insecure_message(resolved_redirect):
        console.print(f"[yellow]Warning:[/yellow] {insecure_msg}")

    def on_success(payload: dict) -> str:
        summary = format_oauth_result(payload)
        team = payload.get("team") or {}
        workspace_id = team.get("id", "")
        bot_token = payload.get("access_token") or (payload.get("bot") or {}).get("bot_access_token") or ""

        console.print("\n[green]OAuth install succeeded[/green]")
        console.print(summary)

        hook = payload.get("incoming_webhook") or {}
        resolved_channel = channel_id.strip() or hook.get("channel_id", "")
        resolved_webhook = webhook_url.strip() or hook.get("url", "")

        if family_slug.strip():
            if not workspace_id:
                console.print("[yellow]No workspace id in response — binding skipped.[/yellow]")
            elif not resolved_channel:
                console.print(
                    "[yellow]Pass --channel-id (or install with incoming webhook) to save family binding.[/yellow]"
                )
            else:
                admin = FamilyAdminService()
                try:
                    detail = admin.add_slack_binding(
                        slug=family_slug.strip(),
                        name=family_name.strip() or family_slug.strip(),
                        workspace_id=workspace_id,
                        channel_id=resolved_channel,
                        webhook_url=resolved_webhook,
                        bot_token=bot_token,
                    )
                except ValueError as exc:
                    console.print(f"[red]Binding failed:[/red] {exc}")
                    raise typer.Exit(1) from exc
                console.print(f"[green]Saved slack_bindings[/green] for family `{detail.slug}`")
                console.print(f"  Workspace: {workspace_id}")
                console.print(f"  Channel: {resolved_channel}")
        elif workspace_id and bot_token:
            admin = FamilyAdminService()
            try:
                result = admin.bind_workspace(
                    workspace_id=workspace_id,
                    bot_token=bot_token,
                    channel_id=resolved_channel,
                    webhook_url=resolved_webhook,
                )
            except ValueError as exc:
                console.print(f"[red]Workspace binding failed:[/red] {exc}")
                raise typer.Exit(1) from exc
            console.print(f"[green]Saved workspace binding[/green] (no household yet)")
            console.print(f"  Workspace: {result['workspace_id']}")
            if result["channel_id"]:
                console.print(f"  Default channel: {result['channel_id']}")
            console.print(
                "\n[dim]Users in Slack should reply `start` to create their household. "
                "Restart the bot after binding.[/dim]"
            )
        else:
            console.print(
                "\n[yellow]No workspace token in OAuth response — binding skipped.[/yellow]"
            )
            console.print(
                "\n[dim]Next: mealprepper slack bind-workspace "
                f"--workspace-id {workspace_id or 'T...'} "
                f"--channel-id {resolved_channel or '<C...>'} --bot-token <xoxb-...>[/dim]"
            )
        return summary

    if print_authorize_url:
        authorize_url = build_authorize_url(client_id=client_id, redirect_uri=resolved_redirect)
        console.print("[bold]1. Add this Redirect URL in api.slack.com → OAuth & Permissions:[/bold]")
        console.print(f"   {resolved_redirect}")
        console.print("\n[bold]2. Open this URL (workspace admin must approve):[/bold]")
        console.print(authorize_url)
        console.print("\n[bold]3. Waiting for OAuth callback...[/bold]")

    try:
        run_oauth_server(
            host=host,
            port=port,
            redirect_uri=resolved_redirect,
            client_id=client_id,
            client_secret=client_secret,
            on_success=on_success,
        )
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc


@slack_app.command("authorize-url")
def slack_authorize_url(
    redirect_uri: str = typer.Option(
        "",
        "--redirect-uri",
        help="Registered redirect URL (default: SLACK_OAUTH_REDIRECT_URI from .env)",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Print the OAuth v2 authorize URL for manual install in another workspace."""
    _setup_logging(verbose)
    from mealprepper.skills.comms.slack_oauth import (
        build_authorize_url,
        slack_redirect_insecure_message,
    )

    settings = get_settings()
    client_id = settings.slack_client_id.strip()
    if not client_id:
        console.print("[red]Set SLACK_CLIENT_ID in .env[/red]")
        raise typer.Exit(1)

    resolved_redirect = redirect_uri.strip() or settings.slack_oauth_redirect_uri.strip()
    if not resolved_redirect:
        console.print(
            "[red]Set --redirect-uri or SLACK_OAUTH_REDIRECT_URI in .env[/red]\n"
            "Example: SLACK_OAUTH_REDIRECT_URI=https://xxxx.ngrok-free.app/slack/oauth/callback"
        )
        raise typer.Exit(1)
    if insecure_msg := slack_redirect_insecure_message(resolved_redirect):
        console.print(f"[yellow]Warning:[/yellow] {insecure_msg}")

    console.print(build_authorize_url(client_id=client_id, redirect_uri=resolved_redirect))


@slack_app.command("bind-workspace")
def slack_bind_workspace(
    workspace_id: str = typer.Option(..., "--workspace-id", help="Slack team_id (workspace)"),
    bot_token: str = typer.Option(..., "--bot-token", help="Bot User OAuth token (xoxb-...)"),
    channel_id: str = typer.Option("", "--channel-id", help="Default Slack channel id (optional)"),
    webhook_url: str = typer.Option("", "--webhook-url", help="Optional incoming webhook URL"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Register a Slack workspace install before any household (family) exists."""
    _setup_logging(verbose)
    admin = FamilyAdminService()
    try:
        result = admin.bind_workspace(
            workspace_id=workspace_id,
            bot_token=bot_token,
            channel_id=channel_id,
            webhook_url=webhook_url,
        )
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    console.print(f"[green]Workspace binding saved[/green] for `{result['workspace_id']}`")
    if result["channel_id"]:
        console.print(f"  Default channel: {result['channel_id']}")
    if result.get("family_id"):
        console.print(f"  Linked family: {result['family_id']}")
    else:
        console.print("  Household: [yellow]pending[/yellow] — users can say `start` in Slack")
    console.print("\nRestart the bot: [bold]mealprepper watch-messages[/bold]")


@slack_app.command("list-workspaces")
def slack_list_workspaces(
    households: bool = typer.Option(
        False,
        "--households",
        help="Also list per-user households for workspace-only installs",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """List Slack workspace bindings (including pending household onboarding)."""
    _setup_logging(verbose)
    admin = FamilyAdminService()
    bindings = admin.list_workspace_bindings()
    if not bindings:
        console.print("[yellow]No workspace bindings.[/yellow] Run `mealprepper slack oauth-server`.")
        return

    table = Table(title="Slack workspace bindings")
    table.add_column("Workspace")
    table.add_column("Channel")
    table.add_column("Household")
    table.add_column("Bot token")
    if verbose:
        table.add_column("Per-user households")
    user_households = admin.list_slack_user_households()
    households_by_workspace: dict[str, list] = {}
    for row in user_households:
        households_by_workspace.setdefault(row.workspace_id, []).append(row)
    for row in bindings:
        household = row["family_slug"] or row["family_name"] or "[dim]pending[/dim]"
        per_user = households_by_workspace.get(row["workspace_id"], [])
        cells = [
            row["workspace_id"],
            row["channel_id"] or "—",
            household,
            "yes" if row["bot_token_set"] else "no",
        ]
        if verbose:
            cells.append(str(len(per_user)) if per_user else "0")
        table.add_row(*cells)
    console.print(table)

    if households and user_households:
        console.print()
        hh_table = Table(title="Per-user households")
        hh_table.add_column("Workspace")
        hh_table.add_column("Slack user")
        hh_table.add_column("Family")
        for row in user_households:
            hh_table.add_row(row.workspace_id, row.slack_user_id, f"{row.family_name} ({row.family_slug})")
        console.print(hh_table)
    elif households:
        console.print("[dim]No per-user households yet — users can say `start` in Slack.[/dim]")


@app.command("watch-messages")
def watch_messages(
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Listen for inbound Slack messages (Socket Mode) and run bot commands."""
    _setup_logging(verbose)
    settings = get_settings()
    backend = settings.comms_backend.lower()

    if backend != "slack":
        console.print(
            "[yellow]COMMS_BACKEND is not 'slack'.[/yellow] "
            "Set COMMS_BACKEND=slack and configure SLACK_BOT_TOKEN + SLACK_APP_TOKEN.\n"
            "See docs/SLACK_BOT.md for setup."
        )
        raise typer.Exit(1)

    try:
        from mealprepper.skills.comms.slack_bot import SlackBotListener
    except RuntimeError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    console.print("[green]Starting MealPrepper Slack bot[/green] (Ctrl+C to stop)")
    if settings.slack_channel_id:
        console.print(
            f"  Legacy channel filter: {settings.slack_channel_id} "
            "[dim](ignored for OAuth-registered workspaces)[/dim]"
        )
    else:
        console.print("  [dim]No SLACK_CHANNEL_ID — responding in any channel for bound workspaces[/dim]")

    SlackBotListener(settings=settings).run()


def main() -> None:
    app()


if __name__ == "__main__":
    main()
