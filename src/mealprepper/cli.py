from __future__ import annotations

import logging
from datetime import date
from typing import Optional

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.table import Table

from mealprepper.config import get_settings
from mealprepper.orchestration.supervisor import MealPrepperSupervisor
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


def _parse_date(value: Optional[str]) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


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
        md_path = out_dir / f"grocery-{grocery.week_label.replace(' ', '')}.md"
        from mealprepper.skills.grocery_builder import GroceryBuilderSkill

        text = GroceryBuilderSkill().render_text(grocery)
        md_path.write_text(text, encoding="utf-8")
        console.print(f"Saved: {md_path}")
    for msg in state.messages:
        console.print(f"  • {msg}")


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


@app.command("show-plan")
def show_plan(
    plan_id: Optional[str] = typer.Option(None, "--plan-id"),
    markdown: bool = typer.Option(False, "--markdown", "-m", help="Show full playbook markdown"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
) -> None:
    """Display the latest or specified weekly plan."""
    _setup_logging(verbose)
    store = SQLiteStore()
    plan = store.get_weekly_plan(plan_id) if plan_id else store.get_latest_plan()

    if not plan:
        console.print("[yellow]No plan found. Run plan-week first.[/yellow]")
        raise typer.Exit(1)

    if markdown and plan.playbook_markdown:
        console.print(Markdown(plan.playbook_markdown))
        return

    table = Table(title=f"Week {plan.week_start} — {plan.week_end}")
    table.add_column("Day")
    table.add_column("Block")
    table.add_column("Meal")
    table.add_column("Prep")
    for meal in plan.meals:
        r = meal.recipe
        table.add_row(
            meal.day.title(),
            meal.meal_block.replace("_", " "),
            r.title,
            f"{r.prep_minutes + r.cook_minutes}m",
        )
    console.print(table)
    console.print(f"Status: {plan.status.value} | ID: {plan.id}")


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
