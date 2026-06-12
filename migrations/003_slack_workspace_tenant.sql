-- Workspace-centric Slack tenancy: one family per Slack workspace.
-- channel_id remains the default notification / interaction channel.

ALTER TABLE slack_bindings ADD COLUMN bot_token TEXT DEFAULT '';

CREATE UNIQUE INDEX IF NOT EXISTS idx_slack_bindings_workspace
    ON slack_bindings(workspace_id);
