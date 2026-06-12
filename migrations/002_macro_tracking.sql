-- Phase 2 foundation: macro tracking schema (columns + daily rollups table).

-- Per-member macro goals (protein_g, carbs_g, fat_g, calories, track_macros flag).
-- NULL means inherit household defaults or no tracking.
ALTER TABLE family_members ADD COLUMN macro_goals_json TEXT DEFAULT NULL;

-- Household macro tracking opt-in and defaults.
-- Example: {"enabled": true, "default_protein_g": 150, "show_in_daily": true}
ALTER TABLE family_settings ADD COLUMN macro_tracking_json TEXT DEFAULT '{}';

-- Cached per-serving nutrition on saved recipes (LLM estimate or USDA lookup).
ALTER TABLE recipe_repository ADD COLUMN nutrition_json TEXT DEFAULT NULL;

-- Daily planned vs actual macro rollups (populated by MacroTrackerSkill).
CREATE TABLE IF NOT EXISTS daily_macro_logs (
    id TEXT PRIMARY KEY,
    family_id TEXT NOT NULL REFERENCES families(id),
    member_id TEXT,
    log_date TEXT NOT NULL,
    planned_json TEXT NOT NULL DEFAULT '{}',
    actual_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(family_id, member_id, log_date)
);

CREATE INDEX IF NOT EXISTS idx_daily_macro_logs_family_date
    ON daily_macro_logs(family_id, log_date);
