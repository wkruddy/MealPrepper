from __future__ import annotations

from mealprepper.models.family import MemberRole
from mealprepper.orchestration.supervisor import MealPrepperSupervisor
from mealprepper.services.family_prompts import meal_finder_system_prompt, weekly_meals_system_prompt
from mealprepper.services.family_resolver import FamilyResolver
from mealprepper.storage.sqlite import SQLiteStore


def test_default_family_context_loaded_from_db():
    resolver = FamilyResolver()
    ctx = resolver.for_family_slug("default")

    assert ctx.family_id == "default"
    assert len(ctx.profile.members) >= 3
    roles = {member.role for member in ctx.profile.members}
    assert MemberRole.TODDLER in roles
    assert MemberRole.INFANT in roles
    assert MemberRole.ADULT in roles
    assert ctx.profile.meal_blocks
    assert ctx.pantry.on_hand or ctx.pantry.weekly_staples
    assert ctx.planning.max_meal_repeat_days >= 1


def test_supervisor_wires_family_context():
    ctx = FamilyResolver().for_family_slug("default")
    store = SQLiteStore(family_id=ctx.family_id)
    supervisor = MealPrepperSupervisor(store=store, family_context=ctx)

    assert supervisor.family_context.family_id == "default"
    assert supervisor.weekly.family_context is ctx
    assert supervisor.grocery.family_context is ctx
    assert supervisor.comms.family_context is ctx
    assert supervisor.weekly.family_context.profile is ctx.profile
    assert supervisor.grocery.inventory.pantry is ctx.pantry


def test_dynamic_prompts_cover_default_household_roles():
    ctx = FamilyResolver().for_family_slug("default")

    meal_prompt = meal_finder_system_prompt(ctx)
    weekly_prompt = weekly_meals_system_prompt(ctx)

    assert "toddler" in meal_prompt.lower()
    assert "infant" in meal_prompt.lower()
    assert "adult" in meal_prompt.lower()
    assert "toddler" in weekly_prompt.lower()
    assert "infant" in weekly_prompt.lower()
    assert "bulk" in weekly_prompt.lower()
