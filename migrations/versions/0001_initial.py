"""Persist canonical snapshots, confirmed mappings, export previews and write journal."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "playlists",
        sa.Column("id", sa.String(500), primary_key=True),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "mappings",
        sa.Column("source_id", sa.String(500), primary_key=True),
        sa.Column("provider", sa.String(32), primary_key=True),
        sa.Column("target", sa.JSON(), nullable=False),
    )
    op.create_table(
        "operations",
        sa.Column("key", sa.String(200), primary_key=True),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "plans",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade():
    op.drop_table("plans")
    op.drop_table("operations")
    op.drop_table("mappings")
    op.drop_table("playlists")
