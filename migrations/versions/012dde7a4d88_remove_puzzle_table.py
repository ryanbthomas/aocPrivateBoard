"""Remove obsolete stored puzzle statements.

Revision ID: 012dde7a4d88
Revises: ddc45664563f
"""

from collections.abc import Sequence

from alembic import op

revision: str = "012dde7a4d88"
down_revision: str | Sequence[str] | None = "ddc45664563f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("puzzles", if_exists=True)


def downgrade() -> None:
    raise RuntimeError("Migrations are up-only; restore a database backup instead.")
