"""Allow the provider namespace in addition to a 500-character playlist ID."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("playlists", "id", existing_type=sa.String(500), type_=sa.String(600))


def downgrade():
    # PostgreSQL refuses to truncate oversized keys rather than losing identities.
    op.alter_column("playlists", "id", existing_type=sa.String(600), type_=sa.String(500))
