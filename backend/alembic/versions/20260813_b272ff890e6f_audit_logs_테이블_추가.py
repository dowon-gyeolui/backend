"""audit_logs 테이블 추가

관리자 감사 로그(ADM-LOG-001) 저장소. 추가 전용 테이블이라 기존 테이블·데이터는
전혀 건드리지 않는다.

autogenerate 결과를 두 군데 손봤다.
- `astext_type=Text()` → `sa.Text()`. 생성기가 임포트되지 않은 이름을 뱉었다(그대로 두면 NameError).
- 인덱스 생성을 `batch_alter_table` → `op.create_index`. SQLite 로 autogenerate 해서
  batch 모드로 나왔을 뿐이고, 신규 테이블이라 batch 가 필요 없다. baseline 리비전과 형태를 맞춘다.

Revision ID: b272ff890e6f
Revises: 1730ca475367
Create Date: 2026-08-13 23:35:32.323354

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'b272ff890e6f'
down_revision: Union[str, None] = '1730ca475367'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('audit_logs',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('admin_id', sa.Integer(), nullable=False),
    sa.Column('menu', sa.String(length=50), nullable=False),
    sa.Column('target_type', sa.String(length=50), nullable=False),
    sa.Column('target_id', sa.String(length=64), nullable=True),
    sa.Column('action', sa.String(length=50), nullable=False),
    sa.Column('before', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('after', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('ip', sa.String(length=45), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_audit_logs_action'), 'audit_logs', ['action'], unique=False)
    op.create_index(op.f('ix_audit_logs_admin_id'), 'audit_logs', ['admin_id'], unique=False)
    op.create_index(op.f('ix_audit_logs_created_at'), 'audit_logs', ['created_at'], unique=False)
    op.create_index(op.f('ix_audit_logs_id'), 'audit_logs', ['id'], unique=False)
    op.create_index('ix_audit_logs_target', 'audit_logs', ['target_type', 'target_id'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_audit_logs_target', table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_id'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_created_at'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_admin_id'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_action'), table_name='audit_logs')
    op.drop_table('audit_logs')
