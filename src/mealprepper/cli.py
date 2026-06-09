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


def _grocery_markdown_path(grocery: GroceryList) -> Path:
    filename = f"grocery-{grocery.week_label.replace(' ', '')}.md"
    return get_settings().data_dir / filename


@app.command("init-db")
def init_db(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Enable debug logging"),
) -> None:
    """Initialize the SQLite database schema."""
    _setup_logging(verbose)
    settings = get_settings()
    store = SQLiteStore()
    console.print(f"[green]Database ready:[/green] {settings.database_path}")
    console.print(f"Tables initialized at {store.db_path}")


@app.command("plan-week")
def plan_week(
    week_start: Optional[str] = typer.Option(
        None, "--week-start", help="Monday of target week (YYYY-MM-DD)"
    ),
    auto_approve: bool = typer.Option(
        False, "--auto-approve", help="Skip notification approval step (dev/testing)"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Generate the weekly meal plan (Saturday workflow)."""
    _setup_logging(verbose)
    supervisor = MealPrepperSupervisor()
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
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Build grocery list from approved plan (Sunday workflow)."""
    _setup_logging(verbose)
    supervisor = MealPrepperSupervisor()
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
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Display the latest or specified grocery list."""
    _setup_logging(verbose)
    store = SQLiteStore()

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
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Send morning notification with today's meals."""
    _setup_logging(verbose)
    supervisor = MealPrepperSupervisor()
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
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Process pending feedback into preferences, or parse a message."""
    _setup_logging(verbose)
    supervisor = MealPrepperSupervisor()

    if message:
        state = supervisor.handle_message(message)
    else:
        state = supervisor.process_feedback()

    for msg in state.messages:
        console.print(f"  • {msg}")


def _load_plan(plan_id: Optional[str]):
    store = SQLiteStore()
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
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Show leftover timing rules and any shelf-life issues for a plan."""
    _setup_logging(verbose)
    plan = _load_plan(plan_id)
    text = FoodShelfLifeSkill().render_audit(plan)
    console.print(Markdown(text))
    console.print(f"Plan ID: {plan.id}")


@app.command("show-synergy")
def show_synergy(
    plan_id: Optional[str] = typer.Option(None, "--plan-id"),
    markdown: bool = typer.Option(False, "--markdown", "-m", help="Render as markdown"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Show cook reuse links, shared ingredients, and synergy notes for a plan."""
    _setup_logging(verbose)
    plan = _load_plan(plan_id)
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
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Display the latest or specified weekly plan."""
    _setup_logging(verbose)
    plan = _load_plan(plan_id)

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
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Import a family recipe or meal idea into the searchable recipe library."""
    _setup_logging(verbose)
    repo = RecipeRepositorySkill()
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
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """List or search the family recipe library."""
    _setup_logging(verbose)
    repo = RecipeRepositorySkill()
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
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Import recipes from config/recipe_sources.yaml (files, URLs, Trello export)."""
    _setup_logging(verbose)
    repo = RecipeRepositorySkill()
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
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Remove a single saved recipe from the family library."""
    _setup_logging(verbose)
    repo = RecipeRepositorySkill()
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

    repo = RecipeRepositorySkill()
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
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Manually approve a pending weekly plan."""
    _setup_logging(verbose)
    supervisor = MealPrepperSupervisor()
    state = supervisor.approve_plan(plan_id=plan_id)
    if state.last_error:
        console.print(f"[red]{state.last_error}[/red]")
        raise typer.Exit(1)
    for msg in state.messages:
        console.print(f"  • {msg}")


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
        console.print(f"  Channel filter: {settings.slack_channel_id}")
    else:
        console.print("  [dim]No SLACK_CHANNEL_ID — responding to @mentions and commands in any channel[/dim]")

    SlackBotListener(settings=settings).run()


def main() -> None:
    app()


if __name__ == "__main__":
    main()
