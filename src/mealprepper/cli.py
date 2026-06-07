from __future__ import annotations

import logging
import re
from datetime import date
from typing import Optional

from pathlib import Path

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

from mealprepper.config import get_settings
from mealprepper.models.grocery import GroceryList
from mealprepper.orchestration.supervisor import MealPrepperSupervisor
from mealprepper.skills.cook_efficiency import CookEfficiencySkill
from mealprepper.skills.grocery_builder import GroceryBuilderSkill
from mealprepper.skills.playbook_renderer import PlaybookRendererSkill
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
        False, "--auto-approve", help="Skip SMS approval (dev/testing)"
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
    """Send morning SMS with today's meals."""
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
        None, "--message", "-m", help="Simulate inbound SMS feedback/approval"
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
    skill = CookEfficiencySkill()
    text = skill.render_report(plan)
    if markdown:
        console.print(Markdown(text))
    else:
        console.print(Markdown(text))


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
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Display the latest or specified weekly plan."""
    _setup_logging(verbose)
    plan = _load_plan(plan_id)

    renderer = PlaybookRendererSkill()

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
    """Watch for inbound SMS messages (stub — integrate Twilio webhook later)."""
    _setup_logging(verbose)
    console.print(
        "[yellow]watch-messages is a stub.[/yellow]\n"
        "Use [bold]process-feedback -m 'APPROVE'[/bold] or "
        "[bold]process-feedback -m 'loved chicken'[/bold] to simulate inbound SMS.\n"
        "For production, expose a webhook that calls process-feedback with --message."
    )


def main() -> None:
    app()


if __name__ == "__main__":
    main()
