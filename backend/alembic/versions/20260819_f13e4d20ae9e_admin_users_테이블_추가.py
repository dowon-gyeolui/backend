"""admin_users 테이블 추가

관리자 계정(T-E01). 앱 사용자(`users`)와 분리된 신규 테이블이라 기존 데이터·스키마는
전혀 건드리지 않는다.

autogenerate 결과에서 한 군데만 손봤다 — 인덱스 생성을 `batch_alter_table` →
`op.create_index`. SQLite 로 autogenerate 해서 batch 모드로 나왔을 뿐이고, 신규
테이블에는 batch 가 필요 없다(audit_logs·star_ledger 리비전과 같은 처리).

`role` CHECK 는 모델 `__table_args__` 에서 그대로 넘어왔다. 신규 테이블의 CHECK 는
`create_table` 에 포함되므로, star_ledger 때처럼 손으로 더할 필요가 없었다.

이 리비전은 계정을 **만들지 않는다.** 3계정 발급은 T-E00 의 스크립트가 하고,
운영 DB 실행은 사람이 한다.

Revision ID: f13e4d20ae9e
Revises: 3ba1e07599fe
Create Date: 2026-08-19 08:50:56.989731

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f13e4d20ae9e'
down_revision: Union[str, None] = '3ba1e07599fe'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('admin_users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('name', sa.String(length=50), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('role', sa.String(length=20), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('must_change_password', sa.Boolean(), nullable=False),
    sa.Column('last_login_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("role IN ('super_admin', 'viewer')", name='ck_admin_users_role'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_admin_users_email'), 'admin_users', ['email'], unique=True)
    op.create_index(op.f('ix_admin_users_id'), 'admin_users', ['id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_admin_users_id'), table_name='admin_users')
    op.drop_index(op.f('ix_admin_users_email'), table_name='admin_users')
    op.drop_table('admin_users')
