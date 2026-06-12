-- Per-Slack-user household mapping within a workspace (workspace install has no family_id).

CREATE TABLE IF NOT EXISTS family_slack_users (
    workspace_id TEXT NOT NULL,
    slack_user_id TEXT NOT NULL,
    family_id TEXT NOT NULL REFERENCES families(id),
    created_at TEXT NOT NULL,
    PRIMARY KEY (workspace_id, slack_user_id)
);

CREATE INDEX IF NOT EXISTS idx_family_slack_users_family
    ON family_slack_users(family_id);
