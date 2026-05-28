from tortoise import BaseDBAsyncClient

RUN_IN_TRANSACTION = True


async def upgrade(db: BaseDBAsyncClient) -> str:
    return """
        CREATE TABLE IF NOT EXISTS "tasks" (
    "id" UUID NOT NULL PRIMARY KEY,
    "created_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updated_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "notion_page_id" VARCHAR(64) NOT NULL UNIQUE,
    "notion_task_id" INT,
    "title" TEXT NOT NULL,
    "project" VARCHAR(128),
    "status" VARCHAR(128),
    "url" TEXT NOT NULL,
    "decision" VARCHAR(32) NOT NULL DEFAULT 'pending',
    "first_seen_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "last_seen_at" TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "decided_at" TIMESTAMPTZ,
    "defer_until" TIMESTAMPTZ,
    "notified_at" TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS "idx_tasks_notion__47f65e" ON "tasks" ("notion_page_id");
CREATE INDEX IF NOT EXISTS "idx_tasks_decisio_95de22" ON "tasks" ("decision");
COMMENT ON TABLE "tasks" IS 'Task discovered in Notion that may become a coding job.';
CREATE TABLE IF NOT EXISTS "aerich" (
    "id" SERIAL NOT NULL PRIMARY KEY,
    "version" VARCHAR(255) NOT NULL,
    "app" VARCHAR(100) NOT NULL,
    "content" JSONB NOT NULL
);"""


async def downgrade(db: BaseDBAsyncClient) -> str:
    return """
        """


MODELS_STATE = (
    "eJztWW1P2zAQ/itRPoHEEITC0DRN6oCNTtBOEDbENEVufG0NiR0SB6gY/30+N2maNClteS"
    "lI/VI19+LYz3Nn5873pi8oeNG6TaIr85Nxb3Lig/qTk68ZJgmCTIoCSdqeNpTKQktIO5Ih"
    "caUSdogXgRJRiNyQBZIJjqY4mEFZ5IobCIEajBtNgUpD9og0fNI32uAKHwxiuIIy3jUuRX"
    "sdB6fCVaMryRPHiTm7jsGRoguyB6Ea7c9fJWacwh1E6WNw5XQYeDSHCKM4gJY7sh9o2dlZ"
    "Y/+btsQ5th1XeLHPM+ugL3uCD83jmNF19EFdFziERAIdAYzHnpcAm4oGM1YCGcYwnCrNBB"
    "Q6JPYQdvNzJ+auBkK/CX9qX8wxIvAtBUwTkSs4ksi4RCzuHwarytaspSa+au+wfrKytbOq"
    "Vyki2Q21UiNiPmhHIsnAVeOaAemGgMt2iBwHdF9pJPOhHNS8ZwFcmriup3/mATkVZChnsZ"
    "zCnMI3H6amWgNtca+fMDgBY7txfHBq149/4kr8KLr2NER1+wA1lpb2C9KVASVCZeIgP4eD"
    "GL8b9qGBj8ZFq3lQJG5oZ1+YOCcSS+FwcesQOhJsqTQFRllmxMYBnZPYvOeS2IUSm0w+45"
    "XrzdUJSBecsl1wr0fCcl7HPQvcKgBfZi98Ipc+uXM84F3ZU487tQlc/qqf6L1wp1bgp5lo"
    "LK16KEMUz85SRBtcTgR0xLEAqFrBFIAmcL1mdnTxPR+szdrH2u7WTm1XmeipDCUfJ4DcaN"
    "oFBCWTHowDZ8NdBXJDh+eJwJdHbNL+cXBu57aONNZWjuvnq7nt46jV/J6aj8Tm3lHrawHR"
    "IBSX4JZs3dXpPeIyF6oLCMNcYm9au1NktrKqTG2ty+MYSSLjaBYYM48lisNvidCbJbsT82"
    "Vul+c2BZdFOMcZonLU56VwHS9hAuBYsD2haMkH55Y1RWxuWZWhiao8lh0WRtKJAPgcH7pj"
    "zstv3TdWxHhkfnaLvkty31Yhg1sanatAzXs+A6+vf2i/ExrTZU9MUoUJhE7MJSv5SniMyZ"
    "zrksoFU4kVrVrIPElZcF1SuQAqsV3euRrp86KgTdyrWxJSZ0wjLFFlO67yLb8oIZx0NQ+I"
    "Js4zuaeoQ8jcnllyg5Fo1ibdYZDM5rFLjGpin/lqobIFNG3bJ4nlhbbRnqXpU32TcAPhrF"
    "XNiMt7KRbzBY21vT1FRaOsKksarcvXNJgaM4CYmL9PADc3NqZpV2xsVLcrUJcHUL1RAi85"
    "wX6ctpoVl1mZS/HkYq40/hkei6bp5b6t9gWud3L7otipKBxEOAC2LxZ6sDz8Byo48PM="
)
