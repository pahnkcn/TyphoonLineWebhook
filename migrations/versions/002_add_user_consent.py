"""Add user_consent table for PDPA compliance

Revision ID: 002
Revises: 001
Create Date: 2026-02-06

Stores user consent timestamps and data deletion requests.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '002'
down_revision: Union[str, None] = '001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'user_consent',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.String(50), nullable=False, unique=True),
        sa.Column('consent_given_at', sa.DateTime, nullable=True),
        sa.Column('consent_revoked_at', sa.DateTime, nullable=True),
        sa.Column('data_deleted_at', sa.DateTime, nullable=True),
        sa.Column('updated_at', sa.DateTime, nullable=False,
                  server_default=sa.text('CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP')),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
        mysql_collate='utf8mb4_unicode_ci',
    )
    op.create_index('idx_uc_user_id', 'user_consent', ['user_id'], unique=True)


def downgrade() -> None:
    op.drop_table('user_consent')
