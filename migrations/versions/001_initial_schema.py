"""Initial schema — conversations, follow_ups, user_metrics, registration_codes

Revision ID: 001
Revises: None
Create Date: 2026-02-06

Captures the existing database schema so that future changes
can be managed via Alembic migrations.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'conversations',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.String(50), nullable=False),
        sa.Column('timestamp', sa.DateTime, nullable=False),
        sa.Column('user_message', sa.Text, nullable=False),
        sa.Column('bot_response', sa.Text, nullable=False),
        sa.Column('token_count', sa.Integer, server_default='0'),
        sa.Column('important_flag', sa.Boolean, server_default='0'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
        mysql_collate='utf8mb4_unicode_ci',
    )
    op.create_index('idx_user_id', 'conversations', ['user_id'])
    op.create_index('idx_timestamp', 'conversations', ['timestamp'])
    op.create_index('idx_important', 'conversations', ['important_flag'])
    op.create_index('idx_user_timestamp', 'conversations', ['user_id', 'timestamp'])

    op.create_table(
        'follow_ups',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.String(50), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('created_at', sa.DateTime, nullable=False),
        sa.Column('updated_at', sa.DateTime, nullable=False),
        sa.Column('scheduled_date', sa.DateTime, nullable=True),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
        mysql_collate='utf8mb4_unicode_ci',
    )
    op.create_index('idx_fu_user_id', 'follow_ups', ['user_id'])
    op.create_index('idx_fu_status', 'follow_ups', ['status'])
    op.create_index('idx_fu_scheduled', 'follow_ups', ['scheduled_date'])
    op.create_index('idx_fu_user_status', 'follow_ups', ['user_id', 'status'])

    op.create_table(
        'user_metrics',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.String(50), nullable=False),
        sa.Column('metric_name', sa.String(50), nullable=False),
        sa.Column('metric_value', sa.Float, nullable=False),
        sa.Column('timestamp', sa.DateTime, nullable=False),
        sa.UniqueConstraint('user_id', 'metric_name', name='unique_user_metric'),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
        mysql_collate='utf8mb4_unicode_ci',
    )
    op.create_index('idx_um_user_id', 'user_metrics', ['user_id'])
    op.create_index('idx_um_metric_name', 'user_metrics', ['metric_name'])

    op.create_table(
        'registration_codes',
        sa.Column('code', sa.String(10), primary_key=True),
        sa.Column('user_id', sa.String(50), nullable=True),
        sa.Column('created_at', sa.DateTime, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('verified_at', sa.DateTime, nullable=True),
        sa.Column('status', sa.Enum('pending', 'verified', 'expired', name='reg_status'),
                  server_default='pending'),
        sa.Column('form_data', sa.JSON, nullable=True),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
        mysql_collate='utf8mb4_unicode_ci',
    )
    op.create_index('idx_rc_user_id', 'registration_codes', ['user_id'])
    op.create_index('idx_rc_status', 'registration_codes', ['status'])


def downgrade() -> None:
    op.drop_table('registration_codes')
    op.drop_table('user_metrics')
    op.drop_table('follow_ups')
    op.drop_table('conversations')
