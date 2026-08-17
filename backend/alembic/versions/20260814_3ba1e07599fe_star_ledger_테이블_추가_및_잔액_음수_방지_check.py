"""star_ledger 테이블 추가 및 잔액 음수 방지 CHECK

스타 재화 원장(T-B06). 신규 테이블이라 기존 데이터는 건드리지 않고, 기존 테이블에
대한 변경은 `users` 에 CHECK 제약 하나를 더하는 것뿐이다.

autogenerate 결과를 두 군데 손봤다.
- 인덱스 생성을 `batch_alter_table` → `op.create_index`. SQLite 로 autogenerate 해서
  batch 모드로 나왔을 뿐이고, 신규 테이블이라 batch 가 필요 없다(audit_logs 와 동일).
- **`users` 의 CHECK 제약을 손으로 추가했다.** alembic 은 CHECK 제약을 autogenerate
  하지 않으므로 여기 없으면 모델에만 있고 DB 에는 영원히 안 생긴다.
  이쪽은 기존 테이블이라 batch 가 필요하다 — SQLite 는 `ALTER TABLE ADD CONSTRAINT`
  가 없어 테이블을 다시 만들어야 하고, Postgres 에서는 batch 가 그냥 통과해
  `ALTER TABLE ... ADD CONSTRAINT` 하나로 나간다.

**운영 DB 적용 전 확인**: `users.star_balance` 에 음수 행이 이미 있으면 CHECK 추가가
실패한다. `SELECT count(*) FROM users WHERE star_balance < 0` 이 0 인지 먼저 볼 것.

Revision ID: 3ba1e07599fe
Revises: b272ff890e6f
Create Date: 2026-08-14 23:57:37.611561

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3ba1e07599fe'
down_revision: Union[str, None] = 'b272ff890e6f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_USERS_BALANCE_CHECK = "ck_users_star_balance_non_negative"


def upgrade() -> None:
    op.create_table('star_ledger',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('entry_type', sa.String(length=20), nullable=False),
    sa.Column('reference_id', sa.String(length=64), nullable=False),
    sa.Column('amount', sa.Integer(), nullable=False),
    sa.Column('balance_after', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('balance_after >= 0', name='ck_star_ledger_balance_after_non_negative'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('reference_id', 'entry_type', name='uq_star_ledger_idempotency')
    )
    op.create_index(op.f('ix_star_ledger_created_at'), 'star_ledger', ['created_at'], unique=False)
    op.create_index(op.f('ix_star_ledger_id'), 'star_ledger', ['id'], unique=False)
    op.create_index(op.f('ix_star_ledger_user_id'), 'star_ledger', ['user_id'], unique=False)

    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_check_constraint(_USERS_BALANCE_CHECK, 'star_balance >= 0')


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_constraint(_USERS_BALANCE_CHECK, type_='check')

    op.drop_index(op.f('ix_star_ledger_user_id'), table_name='star_ledger')
    op.drop_index(op.f('ix_star_ledger_id'), table_name='star_ledger')
    op.drop_index(op.f('ix_star_ledger_created_at'), table_name='star_ledger')
    op.drop_table('star_ledger')
