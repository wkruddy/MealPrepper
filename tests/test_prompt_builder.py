from mealprepper.context.budget import CallType, ContextBudget
from mealprepper.context.prompt_builder import PromptBuilder


def test_prompt_builder_stays_under_budget():
    budget = ContextBudget(max_chars=2000, per_call={"meal_finder": 800})
    builder = PromptBuilder(
        budget=budget,
        call_type=CallType.MEAL_FINDER,
        system="You are a meal planner.",
        task="Plan meals for the week.",
    )
    builder.add_section("Family", "Toddler, two adults, infant BLW", priority=10)
    builder.add_section("Preferences", "Liked: pasta, tacos\nDisliked: liver", priority=20)
    builder.add_section(
        "Past meals",
        "\n".join(f"- Meal {i}: ingredient list here" for i in range(50)),
        priority=40,
    )

    messages = builder.build_messages()
    total = sum(len(m["content"]) for m in messages)
    assert total <= budget.limit_for(CallType.MEAL_FINDER) + len(builder.system)


def test_prompt_builder_prioritizes_sections():
    budget = ContextBudget(max_chars=1000, per_call={"meal_finder": 400})
    builder = PromptBuilder(
        budget=budget,
        call_type=CallType.MEAL_FINDER,
        system="System prompt",
        task="Task line",
    )
    builder.add_section("Critical", "MUST KEEP", priority=1)
    builder.add_section("Optional", "x" * 500, priority=99)

    user = builder.build_user_prompt()
    assert "MUST KEEP" in user
    assert "Task line" in user


def test_build_messages_structure():
    budget = ContextBudget(max_chars=5000)
    builder = PromptBuilder(
        budget=budget,
        call_type=CallType.DEFAULT,
        system="Sys",
        task="Do thing",
    )
    messages = builder.build_messages()
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "Do thing" in messages[1]["content"]
