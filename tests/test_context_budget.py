from mealprepper.context.budget import CallType, ContextBudget


def test_truncate_within_limit():
    budget = ContextBudget(max_chars=1000, per_call={"meal_finder": 500})
    text = "x" * 400
    assert budget.truncate(text, CallType.MEAL_FINDER) == text


def test_truncate_over_limit():
    budget = ContextBudget(max_chars=1000, per_call={"meal_finder": 200})
    text = "a" * 300
    result = budget.truncate(text, CallType.MEAL_FINDER, label="test")
    assert len(result) <= 200
    assert "truncated" in result


def test_limit_for_call_type():
    budget = ContextBudget(max_chars=10000, per_call={"comms": 3000})
    assert budget.limit_for(CallType.COMMS) == 3000
    assert budget.limit_for(CallType.DEFAULT) == 10000


def test_fits():
    budget = ContextBudget(max_chars=100, per_call={"grocery": 50})
    assert budget.fits("short", CallType.GROCERY)
    assert not budget.fits("x" * 60, CallType.GROCERY)


def test_from_config():
    config = {
        "context": {
            "max_context_chars": 8000,
            "budgets": {"meal_finder": 6000, "comms": 2500},
        }
    }
    budget = ContextBudget.from_config(config)
    assert budget.max_chars == 8000
    assert budget.limit_for("meal_finder") == 6000
    assert budget.limit_for("comms") == 2500
