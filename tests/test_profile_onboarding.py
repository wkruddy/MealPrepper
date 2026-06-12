from __future__ import annotations

import pytest

from mealprepper.services.family_settings import FamilySettingsService
from mealprepper.skills.comms.profile_onboarding import (
    ProfileOnboardingFlow,
    parse_cuisine_answer,
    parse_diet_answer,
    parse_eaters_answer,
    parse_fitness_answer,
    primary_name_from_household,
)
from mealprepper.storage.sqlite import SQLiteStore


@pytest.fixture
def admin_db(tmp_path):
    db_path = tmp_path / "profile_onboarding.db"
    SQLiteStore(db_path=db_path)
    return db_path


def test_parse_diet_answer_maps_common_phrases():
    diets, notes = parse_diet_answer("gluten free and dairy free")
    assert "gluten_free" in diets
    assert "dairy_free" in diets
    assert notes


def test_parse_diet_answer_skip():
    diets, notes = parse_diet_answer("skip")
    assert diets == []
    assert notes == ""


def test_parse_fitness_answer():
    assert parse_fitness_answer("trying to cut") == "cut"
    assert parse_fitness_answer("weightlifting") == "weightlifting"
    assert parse_fitness_answer("skip") == ""


def test_parse_cuisine_answer_with_avoid():
    cuisines, likes, avoid = parse_cuisine_answer("love Italian and Mexican, avoid seafood")
    assert "Italian" in cuisines
    assert "Mexican" in cuisines
    assert "seafood" in avoid


def test_parse_eaters_answer():
    members = parse_eaters_answer("just me", "Thom's House")
    assert members == [("Thom", "adult")]

    members = parse_eaters_answer("me and my partner", "Alex's Family")
    assert len(members) == 2
    assert members[0][0] == "Alex"


def test_primary_name_from_household():
    assert primary_name_from_household("Thom's House") == "Thom"
    assert primary_name_from_household("Alex's Family") == "Alex"


def test_profile_onboarding_flow_persists_answers(admin_db):
    from mealprepper.services.family_admin import FamilyAdminService

    admin = FamilyAdminService(db_path=admin_db)
    admin.bind_workspace(
        workspace_id="T_FRACTAL",
        bot_token="xoxb-fractal",
        channel_id="C_HEALTHY",
    )
    detail = admin.create_household_for_slack_user(
        workspace_id="T_FRACTAL",
        slack_user_id="U_ALEX",
        name="Alex's Family",
    )

    settings = FamilySettingsService(db_path=admin_db)
    flow = ProfileOnboardingFlow(settings)
    intro = flow.start(detail.id, household_name=detail.name, thread_ts="100.001")
    assert "dietary" in intro.text.lower()

    step, data = settings.get_profile_onboarding(detail.id)
    assert step == "diet"
    assert data["thread_ts"] == "100.001"

    reply = flow.handle_answer(detail.id, "diet", "gluten free", data)
    assert "nutrition goal" in reply.text.lower()

    step, data = settings.get_profile_onboarding(detail.id)
    reply = flow.handle_answer(detail.id, step, "weightlifting", data)
    assert "food" in reply.text.lower()

    step, data = settings.get_profile_onboarding(detail.id)
    reply = flow.handle_answer(detail.id, step, "Mexican, Mediterranean", data)
    assert "planned for" in reply.text.lower()

    step, data = settings.get_profile_onboarding(detail.id)
    reply = flow.handle_answer(detail.id, step, "just me", data)
    assert "all set" in reply.text.lower()

    summary = settings.get_summary(detail.id)
    assert "gluten_free" in summary.dietary_household
    assert summary.macro_tracking.enabled is True
    assert "Mexican" in summary.cuisine_preferences
    assert len(summary.members) == 1
    assert summary.members[0]["name"] == "Alex"

    step, _ = settings.get_profile_onboarding(detail.id)
    assert step == "complete"
