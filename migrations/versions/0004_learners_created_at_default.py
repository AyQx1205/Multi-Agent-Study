"""learners.created_at 补默认值：让「迁移建表」与「运行时建表」完全一致

背景：`app/store_postgres.py` 里的 SCHEMA_SQL 建的是
`created_at TIMESTAMPTZ NOT NULL DEFAULT now()`，而 0001 基线漏了 `DEFAULT now()`。
两条路径在本地开发（AUTO_APPLY_SCHEMA=true）看不出差别，因为写入 SQL 依赖了这个
默认值；一旦按生产口径只跑迁移（AUTO_APPLY_SCHEMA=false，比如容器里），
第一次创建学习者就会 `null value in column "created_at" violates not-null constraint`。

修复分两层：
1. 应用侧写入显式带上 created_at（不再依赖数据库默认值）；
2. 这条迁移把默认值补上，保证迁移产物与运行时 schema 逐列一致。

Revision ID: 0004_learners_created_at_default
Revises: 0003_security_hardening
Create Date: 2026-11-27
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "0004_learners_created_at_default"
down_revision: Union[str, None] = "0003_security_hardening"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SQLite 不支持 ALTER COLUMN ... SET DEFAULT；那边由应用显式写入时间戳，
    # 所以这里只处理 PostgreSQL。
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE learners ALTER COLUMN created_at SET DEFAULT now()")


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("ALTER TABLE learners ALTER COLUMN created_at DROP DEFAULT")