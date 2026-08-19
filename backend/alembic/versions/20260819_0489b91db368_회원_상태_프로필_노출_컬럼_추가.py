"""회원 상태·프로필 노출 컬럼 추가

관리자 회원 화면(T-E03)이 쓰는 두 컬럼. 기존 컬럼은 건드리지 않는 순수 추가다.

autogenerate 결과에서 두 군데를 손봤다.

1. **`server_default` 를 넣었다.** 둘 다 NOT NULL 인데 기본값 없이 ADD COLUMN 하면
   이미 회원이 있는 DB 에서 그 자리에서 실패한다. 기본값은 "지금까지의 모든 회원은
   정상이고 노출 중"이라는 뜻이라 기존 데이터의 의미가 바뀌지 않는다.
2. **`batch_alter_table` → `op.add_column`.** ADD COLUMN 은 SQLite 도 그대로
   지원해서 테이블 재작성이 필요 없다. batch 는 운영(Postgres)에서도 불필요하다.

`server_default` 는 지운 뒤 남기지 않는다 — 애플리케이션은 항상 값을 채워 넣지만
(모델의 `default`), 운영 중 손으로 넣는 INSERT 가 NOT NULL 위반으로 죽지 않게 한다.

`status` 에 CHECK 를 걸지 않았다. 기존 테이블에 CHECK 를 더하려면 SQLite 에서
테이블을 통째로 재작성해야 하는데(batch), `users` 는 참조가 가장 많은 테이블이라
재작성 비용·위험이 값 3개를 강제하는 이득보다 크다. 값 검증은 API 스키마가 한다.

Revision ID: 0489b91db368
Revises: f13e4d20ae9e
Create Date: 2026-08-19 10:04:11.569409

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0489b91db368'
down_revision: Union[str, None] = 'f13e4d20ae9e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'users',
        sa.Column(
            'status',
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'active'"),
        ),
    )
    op.add_column(
        'users',
        sa.Column(
            'profile_hidden',
            sa.Boolean(),
            nullable=False,
            server_default=sa.text('false'),
        ),
    )


def downgrade() -> None:
    op.drop_column('users', 'profile_hidden')
    op.drop_column('users', 'status')
