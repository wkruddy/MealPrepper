from datetime import date

from mealprepper.models.grocery import GroceryItem, GroceryList
from mealprepper.models.plans import WeeklyPlan, PlanStatus
from mealprepper.storage.sqlite import SQLiteStore


def test_get_latest_grocery(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    store = SQLiteStore(db_path=db_path)

    plan = WeeklyPlan(
        id="plan-1",
        week_start=date(2026, 6, 1),
        week_end=date(2026, 6, 7),
        status=PlanStatus.APPROVED,
    )
    store.save_weekly_plan(plan)

    older = GroceryList(
        weekly_plan_id="plan-1",
        week_label="2026-05-25 — 2026-05-31",
        items=[GroceryItem(name="rice", quantity="1", unit="bag")],
    )
    newer = GroceryList(
        weekly_plan_id="plan-1",
        week_label="2026-06-01 — 2026-06-07",
        items=[GroceryItem(name="oats", quantity="1", unit="bag")],
    )
    store.save_grocery_list(older)
    store.save_grocery_list(newer)

    latest = store.get_latest_grocery()
    assert latest is not None
    assert latest.week_label == "2026-06-01 — 2026-06-07"
    assert latest.items[0].name == "oats"
