from __future__ import annotations

import sqlite3

import pytest

from mealprepper.services.family_admin import FamilyAdminService
from mealprepper.services.family_resolver import FamilyResolver
from mealprepper.storage.sqlite import SQLiteStore


@pytest.fixture
def admin_db(tmp_path):
    db_path = tmp_path / "admin.db"
    SQLiteStore(db_path=db_path)
    return db_path


def test_add_slack_binding_creates_family(admin_db):
    admin = FamilyAdminService(db_path=admin_db)
    detail = admin.add_slack_binding(
        slug="friend",
        name="Friend Family",
        workspace_id="T_FRIEND",
        channel_id="C_FRIEND",
        webhook_url="https://hooks.example/friend",
        bot_token="xoxb-friend",
        timezone="America/Chicago",
    )
    assert detail.slug == "friend"
    assert detail.slack_bindings[0]["workspace_id"] == "T_FRIEND"
    assert detail.slack_bindings[0]["bot_token_set"] is True

    resolver = FamilyResolver(db_path=admin_db)
    assert resolver.bot_token_for_workspace("T_FRIEND") == "xoxb-friend"
    ctx = resolver.for_slack_workspace("T_FRIEND")
    assert ctx.family_id == "friend"


def test_list_and_show_families(admin_db):
    admin = FamilyAdminService(db_path=admin_db)
    admin.add_slack_binding(
        slug="friend",
        name="Friend Family",
        workspace_id="T_FRIEND",
        channel_id="C_FRIEND",
    )
    families = admin.list_families()
    assert any(row.slug == "friend" for row in families)
    friend = next(row for row in families if row.slug == "friend")
    assert friend.slack_workspace_id == "T_FRIEND"
    assert friend.recipe_count == 0
    assert friend.plan_count == 0

    detail = admin.get_family_detail("friend")
    assert detail.name == "Friend Family"
    assert detail.slack_bindings[0]["channel_id"] == "C_FRIEND"
    assert detail.recipe_count == 0
    assert detail.member_count == 0
    assert detail.slack_users == []


def test_list_slack_user_households(admin_db):
    admin = FamilyAdminService(db_path=admin_db)
    admin.bind_workspace(
        workspace_id="T_FRACTAL",
        bot_token="xoxb-fractal",
        channel_id="C_HEALTHY",
    )
    admin.create_household_for_slack_user(
        workspace_id="T_FRACTAL",
        slack_user_id="U_ALEX",
        name="Alex's Family",
    )
    admin.create_household_for_slack_user(
        workspace_id="T_FRACTAL",
        slack_user_id="U_BETH",
        name="Beth's Family",
    )

    families = admin.list_families()
    alex = next(row for row in families if row.slug == "alex-s-family")
    assert alex.slack_workspace_id is None
    assert alex.slack_user_links == ["T_FRACTAL:U_ALEX"]

    households = admin.list_slack_user_households()
    assert len(households) == 2
    assert {row.slack_user_id for row in households} == {"U_ALEX", "U_BETH"}

    filtered = admin.list_slack_user_households(workspace_id="T_FRACTAL")
    assert len(filtered) == 2

    detail = admin.get_family_detail("alex-s-family")
    assert len(detail.slack_users) == 1
    assert detail.slack_users[0]["slack_user_id"] == "U_ALEX"

    saved = admin.get_slack_user_household("T_FRACTAL", "U_ALEX")
    assert saved is not None
    assert saved.family_slug == "alex-s-family"
    assert admin.get_slack_user_household("T_FRACTAL", "U_MISSING") is None


def test_delete_family_removes_tenant_data(admin_db):
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
    settings = __import__(
        "mealprepper.services.family_settings", fromlist=["FamilySettingsService"]
    ).FamilySettingsService(db_path=admin_db)
    settings.add_member(detail.id, name="Alex", role="adult")
    settings.set_household_diet(detail.id, ["gluten_free"])

    preview = admin.delete_family(detail.slug, dry_run=True)
    assert preview.dry_run is True
    assert preview.deleted_rows["family_members"] >= 1
    assert admin.get_family_detail(detail.slug).slug == detail.slug

    result = admin.delete_family(detail.slug)
    assert result.dry_run is False
    with pytest.raises(ValueError, match="not found"):
        admin.get_family_detail(detail.slug)

    households = admin.list_slack_user_households()
    assert all(row.family_id != detail.id for row in households)


def test_delete_default_family_requires_force(admin_db):
    admin = FamilyAdminService(db_path=admin_db)
    with pytest.raises(ValueError, match="Refusing to delete"):
        admin.delete_family("default", dry_run=True)


def test_get_family_detail_missing(admin_db):
    admin = FamilyAdminService(db_path=admin_db)
    with pytest.raises(ValueError, match="not found"):
        admin.get_family_detail("missing")


def test_bind_workspace_without_family(admin_db):
    admin = FamilyAdminService(db_path=admin_db)
    result = admin.bind_workspace(
        workspace_id="T_FRACTAL",
        bot_token="xoxb-fractal",
        channel_id="C_FRACTAL",
        webhook_url="https://hooks.example/fractal",
    )
    assert result["workspace_id"] == "T_FRACTAL"
    assert result["family_id"] == ""

    bindings = admin.list_workspace_bindings()
    assert any(b["workspace_id"] == "T_FRACTAL" and not b["family_id"] for b in bindings)


def test_create_household_requires_workspace_binding(admin_db):
    admin = FamilyAdminService(db_path=admin_db)
    with pytest.raises(ValueError, match="not registered"):
        admin.create_household_for_slack_user(
            workspace_id="T_MISSING",
            slack_user_id="U1",
            name="Test Family",
        )


def test_bind_workspace_without_family(admin_db):
    admin = FamilyAdminService(db_path=admin_db)
    result = admin.bind_workspace(
        workspace_id="T_FRACTAL",
        bot_token="xoxb-fractal",
        channel_id="C_HEALTHY",
        webhook_url="https://hooks.slack.com/services/T/B/X",
    )
    assert result["workspace_id"] == "T_FRACTAL"
    assert result["bot_token_set"] is True
    assert result["family_id"] == ""

    resolver = FamilyResolver(db_path=admin_db)
    assert resolver.has_workspace_binding("T_FRACTAL") is True
    assert resolver.bot_token_for_workspace("T_FRACTAL") == "xoxb-fractal"

    from mealprepper.services.family_resolver import WorkspacePendingOnboarding

    with pytest.raises(WorkspacePendingOnboarding):
        resolver.for_slack_workspace("T_FRACTAL")

    bindings = admin.list_workspace_bindings()
    assert len(bindings) == 1
    assert bindings[0]["family_slug"] == ""


def test_bind_workspace_then_add_family(admin_db):
    admin = FamilyAdminService(db_path=admin_db)
    admin.bind_workspace(
        workspace_id="T_FRACTAL",
        bot_token="xoxb-fractal",
        channel_id="C_HEALTHY",
    )
    detail = admin.add_slack_binding(
        slug="fractal",
        name="Fractal Productions",
        workspace_id="T_FRACTAL",
        channel_id="C_HEALTHY",
        bot_token="xoxb-fractal-updated",
    )
    assert detail.slug == "fractal"
    ctx = FamilyResolver(db_path=admin_db).for_slack_workspace("T_FRACTAL")
    assert ctx.family_id == "fractal"
