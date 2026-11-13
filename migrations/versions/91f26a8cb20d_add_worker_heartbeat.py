"""Add persistent worker heartbeat state.

Revision ID: 91f26a8cb20d
Revises: 012dde7a4d88
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "91f26a8cb20d"
down_revision: str | Sequence[str] | None = "012dde7a4d88"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "worker_heartbeat",
        sa.Column("worker_name", sa.Text(), primary_key=True),
        sa.Column("last_cycle_at", sa.Text(), nullable=False),
        sa.Column("last_success_at", sa.Text()),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("detail", sa.Text()),
        sa.CheckConstraint(
            "status IN ('ok', 'error')", name="ck_worker_heartbeat_status"
        ),
        sa.CheckConstraint(
            "consecutive_failures >= 0",
            name="ck_worker_heartbeat_consecutive_failures",
        ),
    )


def downgrade() -> None:
    raise RuntimeError("Migrations are up-only; restore a database backup instead.")
