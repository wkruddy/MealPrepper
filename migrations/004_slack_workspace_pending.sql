-- Workspace-only OAuth install: family_id nullable until household onboarding (`start`).
-- Recreate slack_bindings to drop channel_id UNIQUE (one row per workspace).

CREATE TABLE slack_bindings_new (
    id TEXT PRIMARY KEY,
    family_id TEXT REFERENCES families(id),
    workspace_id TEXT NOT NULL,
    channel_id TEXT NOT NULL DEFAULT '',
    webhook_url TEXT,
    bot_token TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

INSERT INTO slack_bindings_new (id, family_id, workspace_id, channel_id, webhook_url, bot_token, created_at)
SELECT id, family_id, workspace_id, channel_id, webhook_url, COALESCE(bot_token, ''), created_at
FROM slack_bindings;

DROP TABLE slack_bindings;

ALTER TABLE slack_bindings_new RENAME TO slack_bindings;

CREATE UNIQUE INDEX IF NOT EXISTS idx_slack_bindings_workspace ON slack_bindings(workspace_id);
