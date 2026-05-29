from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "task_runs" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "status" VARCHAR(32) NOT NULL DEFAULT 'pending',
    "branch_name" VARCHAR(255),
    "worktree_path" TEXT,
    "mr_url" TEXT,
    "mr_iid" INT,
    "iterations_code" INT NOT NULL DEFAULT 0,
    "iterations_review" INT NOT NULL DEFAULT 0,
    "error_message" TEXT,
    "started_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "finished_at" TIMESTAMPTZ,
    "events" JSONB NOT NULL,
    "task_id" UUID NOT NULL REFERENCES "tasks" ("id") ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS "idx_task_runs_status_b5689f" ON "task_runs" ("status");
COMMENT ON TABLE "task_runs" IS 'Single execution attempt of a Task by the coding agent.';"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        DROP TABLE IF EXISTS "task_runs";"""


MODELS_STATE = (
    "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"
)
