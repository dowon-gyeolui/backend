"""스타 재화 원장 기록 — 잔액을 바꾸는 유일한 경로.

**커밋하지 않는다.** 차감은 카드 생성 성공과 같은 트랜잭션에 묶여야 하므로
(OI-PAY-004 확정) 커밋 시점은 호출자가 정한다.
"""

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.star_ledger import StarLedger
from app.models.user import User


def _insert(db: AsyncSession):
    """중복 멱등키를 조용히 무시하는 INSERT 생성자를 준다.

    `ON CONFLICT DO NOTHING` 은 방언별 `insert()` 에만 있다(Postgres·SQLite 둘 다 지원).
    이걸 쓰는 이유는 동시 요청 때문이다 — "있으면 건너뛴다"를 SELECT 로 먼저 확인하면
    두 요청이 나란히 통과해 이중 지급이 난다. 판정을 DB 의 Unique 제약에 맡긴다.
    """
    if db.get_bind().dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert


async def record(
    db: AsyncSession,
    user: User,
    *,
    entry_type: str,
    reference_id: str,
    amount: int,
) -> bool:
    """원장에 한 줄 남기고 **같은 트랜잭션에서** 잔액을 갱신한다.

    - 반환 `True`  = 이번 호출로 반영됨
    - 반환 `False` = 같은 `(reference_id, entry_type)` 원장이 이미 있어 무시됨
      (버튼 연속 클릭·재시도·webhook 재전송). 잔액은 건드리지 않는다.
    - 잔액이 음수가 되는 요청은 402 로 거절한다.
    """
    # 사용자 행을 잠그고 DB 의 현재 잔액을 읽는다. 메모리의 `user.star_balance` 를 그냥
    # 믿으면 동시 요청에서 `balance_after` 연속성이 깨진다. (SQLite 는 FOR UPDATE 를
    # 무시하지만 커넥션이 하나라 어차피 직렬화된다.)
    locked = await db.execute(
        select(User.star_balance).where(User.id == user.id).with_for_update()
    )
    balance_after = locked.scalar_one() + amount
    if balance_after < 0:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail="스타가 부족합니다.",
        )

    insert = _insert(db)
    result = await db.execute(
        insert(StarLedger)
        .values(
            user_id=user.id,
            entry_type=entry_type,
            reference_id=reference_id,
            amount=amount,
            balance_after=balance_after,
        )
        .on_conflict_do_nothing(
            index_elements=[StarLedger.reference_id, StarLedger.entry_type]
        )
    )
    if result.rowcount == 0:
        return False

    user.star_balance = balance_after
    return True
