"""Add multi_ai_logs table for Multi-AI Consensus tracking

Revision ID: 003
Revises: 002
Create Date: 2026-02-09

Stores per-request Multi-AI consensus results including best provider,
scores, token usage, and timing for dashboard visualization.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '003'
down_revision: Union[str, None] = '002'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'multi_ai_logs',
        sa.Column('id', sa.Integer, primary_key=True, autoincrement=True),
        sa.Column('user_id', sa.String(50), nullable=False),
        sa.Column('timestamp', sa.DateTime, nullable=False),
        sa.Column('best_provider', sa.String(100), nullable=False),
        sa.Column('best_score', sa.Float, nullable=True),
        sa.Column('all_scores', sa.JSON, nullable=True),
        sa.Column('providers_used', sa.Integer, nullable=False),
        sa.Column('generation_time_ms', sa.Float, nullable=True),
        sa.Column('evaluation_time_ms', sa.Float, nullable=True),
        sa.Column('total_time_ms', sa.Float, nullable=True),
        sa.Column('token_usage', sa.JSON, nullable=True),
        sa.Column('provider_times', sa.JSON, nullable=True),
        mysql_engine='InnoDB',
        mysql_charset='utf8mb4',
        mysql_collate='utf8mb4_unicode_ci',
    )
    op.create_index('idx_mai_timestamp', 'multi_ai_logs', ['timestamp'])
    op.create_index('idx_mai_user_id', 'multi_ai_logs', ['user_id'])
    op.create_index('idx_mai_best_provider', 'multi_ai_logs', ['best_provider'])


def downgrade() -> None:
    op.drop_table('multi_ai_logs')
