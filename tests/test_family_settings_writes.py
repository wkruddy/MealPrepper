from __future__ import annotations

import json
import sqlite3

import pytest

from mealprepper.services.family_settings import FamilySettingsService
from mealprepper.storage.sqlite import SQLiteStore


@pytest.fixture
def settings_db(tmp_path):
    db_path = tmp_path / "settings.db"
    SQLiteStore(db_path=db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO families (id, name, slug, timezone, status, created_at, updated_at)
        VALUES ('test', 'Test', 'test', 'America/New_York', 'active', 'now', 'now')
        """
    )
    conn.execute(
        """
        INSERT INTO family_members
        (id, family_id, display_name, role, constraints_json, sort_order)
        VALUES ('a1', 'test', 'Alex', 'adult', '{}', 0)
        """
    )
    conn.commit()
    conn.close()
    return db_path


def test_add_and_remove_member_constraint(settings_db):
    service = FamilySettingsService(db_path=settings_db)
    key = service.add_member_constraint("test", "keto", "Alex")
    assert key == "keto"

    summary = service.get_summary("test")
    assert summary.members[0]["constraints"]["keto"] is True

    removed = service.remove_member_constraint("test", "keto", "Alex")
    assert removed == ["Alex"]


def test_set_household_diet(settings_db):
    service = FamilySettingsService(db_path=settings_db)
    diets = service.set_household_diet("test", ["gluten_free", "dairy_free"])
    assert diets == ["gluten_free", "dairy_free"]

    summary = service.get_summary("test")
    assert summary.dietary_household == ["gluten_free", "dairy_free"]


def test_pantry_add_remove(settings_db):
    service = FamilySettingsService(db_path=settings_db)
    service.add_pantry_item("test", "olive oil")
    summary = service.get_summary("test")
    assert summary.pantry_on_hand_count == 1

    service.remove_pantry_item("test", "olive oil")
    summary = service.get_summary("test")
    assert summary.pantry_on_hand_count == 0


def test_add_member(settings_db):
    service = FamilySettingsService(db_path=settings_db)
    member_id = service.add_member("test", name="Sam", role="adult", age_years=32)
    assert member_id

    summary = service.get_summary("test")
    names = [m["name"] for m in summary.members]
    assert "Sam" in names
