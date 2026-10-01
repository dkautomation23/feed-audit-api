"""runs, findings and run_files

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.Integer, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("rows_read", sa.Integer, nullable=False),
        sa.Column("products", sa.Integer, nullable=False),
        sa.Column("variants", sa.Integer, nullable=False),
        sa.Column("rows_left_out", sa.Integer, nullable=False),
        sa.Column("findings_total", sa.Integer, nullable=False),
        sa.Column("findings_blocking", sa.Integer, nullable=False),
        sa.Column("delimiter", sa.String(4), nullable=False),
        sa.Column("encoding", sa.String(40), nullable=False),
        sa.Column("decimal", sa.String(1), nullable=False),
        sa.UniqueConstraint("sha256", name="runs_sha256_key"),
    )
    op.create_table(
        "findings",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("run_id", sa.Integer, sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("level", sa.String(20), nullable=False),
        sa.Column("row", sa.Integer, nullable=False),
        sa.Column("sku", sa.Text, nullable=False),
        sa.Column("what", sa.String(200), nullable=False),
        sa.Column("detail", sa.Text, nullable=False),
    )
    op.create_index("ix_findings_run_id", "findings", ["run_id"])
    op.create_index("ix_findings_what", "findings", ["what"])
    op.create_table(
        "run_files",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("run_id", sa.Integer, sa.ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.UniqueConstraint("run_id", "kind", name="run_files_run_id_kind_key"),
        sa.CheckConstraint("kind IN ('matrixify', 'report')", name="run_files_kind_check"),
    )


def downgrade() -> None:
    op.drop_table("run_files")
    op.drop_index("ix_findings_what", table_name="findings")
    op.drop_index("ix_findings_run_id", table_name="findings")
    op.drop_table("findings")
    op.drop_table("runs")
