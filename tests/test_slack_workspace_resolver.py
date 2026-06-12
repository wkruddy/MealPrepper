from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone

import pytest

from mealprepper.services.family_resolver import FamilyResolver
from mealprepper.storage.sqlite import SQLiteStore


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


@pytest.fixture
def resolver(tmp_path):
    db_path = tmp_path / "test.db"
    SQLiteStore(db_path=db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT INTO families (id, name, slug, timezone, status, created_at, updated_at)
        VALUES ('friend', 'Friend Family', 'friend', 'America/Chicago', 'active', ?, ?)
        """,
        (_utcnow(), _utcnow()),
    )
    conn.execute(
        """
        INSERT INTO slack_bindings
            (id, family_id, workspace_id, channel_id, webhook_url, created_at)
        VALUES (?, 'friend', 'T_FRIEND', 'C_FRIEND', 'https://hooks.slack.com/friend', ?)
        """,
        (str(uuid.uuid4()), _utcnow()),
    )
    conn.commit()
    conn.close()
    return FamilyResolver(db_path=db_path)


def test_resolve_by_workspace_id(resolver):
    ctx = resolver.for_slack_workspace("T_FRIEND")
    assert ctx.family_id == "friend"
    assert ctx.slack is not None
    assert ctx.slack.workspace_id == "T_FRIEND"
    assert ctx.slack.channel_id == "C_FRIEND"


def test_unknown_workspace_does_not_use_default(resolver):
    with pytest.raises(ValueError, match="not registered"):
        resolver.for_slack_workspace("T_UNKNOWN", "C_UNKNOWN")


def test_channel_lookup_still_works(resolver):
    ctx = resolver.for_slack_channel("C_FRIEND")
    assert ctx.family_id == "friend"
