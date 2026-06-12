-- Phase 0: multi-family tenant schema (structural DDL only; column backfills in Python).

CREATE TABLE IF NOT EXISTS families (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    slug TEXT UNIQUE,
    timezone TEXT NOT NULL DEFAULT 'America/New_York',
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS slack_bindings (
    id TEXT PRIMARY KEY,
    family_id TEXT NOT NULL REFERENCES families(id),
    workspace_id TEXT NOT NULL,
    channel_id TEXT NOT NULL UNIQUE,
    webhook_url TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(workspace_id, channel_id)
);

CREATE TABLE IF NOT EXISTS family_members (
    id TEXT PRIMARY KEY,
    family_id TEXT NOT NULL REFERENCES families(id),
    display_name TEXT NOT NULL,
    role TEXT NOT NULL,
    age_years REAL,
    age_months REAL,
    constraints_json TEXT NOT NULL DEFAULT '{}',
    notes TEXT DEFAULT '',
    sort_order INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS family_settings (
    family_id TEXT PRIMARY KEY REFERENCES families(id),
    meal_blocks_json TEXT NOT NULL DEFAULT '[]',
    schedule_json TEXT NOT NULL DEFAULT '{}',
    planning_json TEXT NOT NULL DEFAULT '{}',
    cuisine_preferences_json TEXT DEFAULT '[]',
    dietary_household_json TEXT DEFAULT '[]',
    staple_patterns_json TEXT DEFAULT '[]',
    onboarding_step TEXT,
    onboarding_data_json TEXT DEFAULT '{}',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS family_pantry (
    family_id TEXT NOT NULL REFERENCES families(id),
    category TEXT NOT NULL,
    item_name TEXT NOT NULL,
    PRIMARY KEY (family_id, category, item_name)
);

CREATE TABLE IF NOT EXISTS slack_conversations (
    id TEXT PRIMARY KEY,
    family_id TEXT NOT NULL REFERENCES families(id),
    channel_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    flow TEXT NOT NULL,
    step TEXT NOT NULL,
    payload_json TEXT NOT NULL DEFAULT '{}',
    expires_at TEXT NOT NULL,
    UNIQUE(channel_id, user_id, flow)
);
