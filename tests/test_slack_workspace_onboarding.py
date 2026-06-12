from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from mealprepper.services.family_admin import FamilyAdminService
from mealprepper.services.family_resolver import FamilyResolver, WorkspacePendingOnboarding
from mealprepper.skills.comms.bot_commands import MealPrepperBotHandler
from mealprepper.storage.sqlite import SQLiteStore


@pytest.fixture
def workspace_db(tmp_path):
    db_path = tmp_path / "workspace.db"
    SQLiteStore(db_path=db_path)
    admin = FamilyAdminService(db_path=db_path)
    admin.bind_workspace(
        workspace_id="T_FRACTAL",
        bot_token="xoxb-fractal",
        channel_id="C_HEALTHY",
        webhook_url="https://hooks.slack.com/fractal",
    )
    return db_path


def test_bind_workspace_without_family(workspace_db):
    admin = FamilyAdminService(db_path=workspace_db)
    bindings = admin.list_workspace_bindings()
    assert len(bindings) == 1
    assert bindings[0]["workspace_id"] == "T_FRACTAL"
    assert bindings[0]["family_id"] == ""
    assert bindings[0]["bot_token_set"] is True


def test_resolve_workspace_pending_without_user(workspace_db):
    resolver = FamilyResolver(db_path=workspace_db)
    with pytest.raises(WorkspacePendingOnboarding):
        resolver.for_slack_workspace("T_FRACTAL", slack_user_id="U_ALEX")


def test_legacy_binding_still_resolves_all_users(workspace_db):
    admin = FamilyAdminService(db_path=workspace_db)
    admin.add_slack_binding(
        slug="hollyw00t",
        name="Hollyw00t",
        workspace_id="T_DEV",
        channel_id="C_DEV",
        bot_token="xoxb-dev",
    )
    resolver = FamilyResolver(db_path=workspace_db)
    ctx = resolver.for_slack_workspace("T_DEV", slack_user_id="U_ANYONE")
    assert ctx.family_id == "hollyw00t"


def test_create_household_for_slack_user(workspace_db):
    admin = FamilyAdminService(db_path=workspace_db)
    detail = admin.create_household_for_slack_user(
        workspace_id="T_FRACTAL",
        slack_user_id="U_ALEX",
        name="Alex's Family",
    )
    assert detail.slug == "alex-s-family"
    assert detail.name == "Alex's Family"

    resolver = FamilyResolver(db_path=workspace_db)
    ctx = resolver.for_slack_workspace("T_FRACTAL", slack_user_id="U_ALEX")
    assert ctx.family_id == "alex-s-family"
    assert ctx.slack is not None
    assert ctx.slack.bot_token == "xoxb-fractal"


def test_two_users_get_two_families(workspace_db):
    admin = FamilyAdminService(db_path=workspace_db)
    alex = admin.create_household_for_slack_user(
        workspace_id="T_FRACTAL",
        slack_user_id="U_ALEX",
        name="Alex's Family",
    )
    beth = admin.create_household_for_slack_user(
        workspace_id="T_FRACTAL",
        slack_user_id="U_BETH",
        name="Beth's Family",
    )
    assert alex.id != beth.id

    resolver = FamilyResolver(db_path=workspace_db)
    assert resolver.for_slack_workspace("T_FRACTAL", slack_user_id="U_ALEX").family_id == alex.id
    assert resolver.for_slack_workspace("T_FRACTAL", slack_user_id="U_BETH").family_id == beth.id


def test_bot_onboarding_start_creates_family(workspace_db):
    handler = MealPrepperBotHandler(
        supervisor=MagicMock(),
        recipe_repo=MagicMock(),
    )
    handler.store = MagicMock()
    handler.store.db_path = workspace_db
    handler.settings_service = __import__(
        "mealprepper.services.family_settings", fromlist=["FamilySettingsService"]
    ).FamilySettingsService(db_path=workspace_db)
    handler._family_admin = FamilyAdminService(db_path=workspace_db)
    handler._profile_onboarding = __import__(
        "mealprepper.skills.comms.profile_onboarding", fromlist=["ProfileOnboardingFlow"]
    ).ProfileOnboardingFlow(handler.settings_service)

    reply = handler.handle(
        "start",
        channel="C_HEALTHY",
        workspace_id="T_FRACTAL",
        slack_user_id="U_ALEX",
        message_ts="100.001",
    )
    assert reply.success
    assert "household" in reply.text.lower()
    assert handler.get_onboarding_thread_ts("T_FRACTAL", "U_ALEX") == "100.001"

    reply = handler.handle(
        "Alex's Family",
        channel="C_HEALTHY",
        workspace_id="T_FRACTAL",
        slack_user_id="U_ALEX",
        thread_ts="100.001",
    )
    assert "confirm" in reply.text.lower()

    reply = handler.handle(
        "confirm",
        channel="C_HEALTHY",
        workspace_id="T_FRACTAL",
        slack_user_id="U_ALEX",
    )
    assert reply.success
    assert "Alex's Family" in reply.text
    assert "dietary" in reply.text.lower()

    resolver = FamilyResolver(db_path=workspace_db)
    ctx = resolver.for_slack_workspace("T_FRACTAL", slack_user_id="U_ALEX")
    assert ctx.family_id == "alex-s-family"


def test_bot_unknown_user_prompts_start(workspace_db):
    handler = MealPrepperBotHandler(
        supervisor=MagicMock(),
        recipe_repo=MagicMock(),
    )
    handler.store = MagicMock()
    handler.store.db_path = workspace_db
    handler.settings_service = __import__(
        "mealprepper.services.family_settings", fromlist=["FamilySettingsService"]
    ).FamilySettingsService(db_path=workspace_db)
    handler._family_admin = FamilyAdminService(db_path=workspace_db)
    handler._profile_onboarding = __import__(
        "mealprepper.skills.comms.profile_onboarding", fromlist=["ProfileOnboardingFlow"]
    ).ProfileOnboardingFlow(handler.settings_service)

    reply = handler.handle(
        "help",
        channel="C_HEALTHY",
        workspace_id="T_FRACTAL",
        slack_user_id="U_NEW",
    )
    assert "start" in reply.text.lower()

    reply = handler.handle(
        "status",
        channel="C_HEALTHY",
        workspace_id="T_FRACTAL",
        slack_user_id="U_NEW",
    )
    assert "start" in reply.text.lower()
