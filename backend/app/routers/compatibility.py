from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.compatibility import CompatibilityReport
from app.services import compatibility as compatibility_service

router = APIRouter()


def _require_birth_data(user: User, *, is_self: bool) -> None:
    if user.birth_date is None:
        who = "자신의" if is_self else "상대의"
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"{who} 생년월일이 먼저 입력되어야 합니다.",
        )


@router.get("/report/{peer_id}", response_model=CompatibilityReport)
async def get_compatibility_report(
    peer_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """운명 분석 리포트 — 채팅 헤더 드로우에서 호출.

    현재 사용자와 peer_id 사이의 궁합 요약(시너지·주의 포인트)과
    인연 키워드 3개를 반환합니다.
    """
    if peer_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="자기 자신과의 리포트는 생성하지 않습니다.",
        )
    _require_birth_data(current_user, is_self=True)

    target = await db.get(User, peer_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"user_id={peer_id} 를 찾을 수 없습니다.",
        )
    _require_birth_data(target, is_self=False)

    return compatibility_service.build_report(current_user, target)
