"""node label (short display name)

Revision ID: 9c1e5b7d2a40
Revises: 3a42fd2adb4d
Create Date: 2026-10-08 10:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = '9c1e5b7d2a40'
down_revision = '3a42fd2adb4d'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('node', schema=None) as batch_op:
        batch_op.add_column(sa.Column('label', sa.String(length=64), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('node', schema=None) as batch_op:
        batch_op.drop_column('label')
