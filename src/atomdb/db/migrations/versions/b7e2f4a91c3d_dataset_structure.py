"""dataset structure summary (pbc, cell, per-atom arrays)

Revision ID: b7e2f4a91c3d
Revises: 9c1e5b7d2a40
Create Date: 2026-10-10 12:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = 'b7e2f4a91c3d'
down_revision = '9c1e5b7d2a40'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('dataset', schema=None) as batch_op:
        batch_op.add_column(sa.Column('structure', sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('dataset', schema=None) as batch_op:
        batch_op.drop_column('structure')
