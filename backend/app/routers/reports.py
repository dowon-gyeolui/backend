"""User-report endpoint — 운명 분석 리포트 drawer 의 신고하기 흐름.

POST /reports stores one row per submitted report. Moderation team can
later read these (admin UI is a Phase 2 task) plus the matching chat
history to decide on action.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.database import get_db
from app.models.report import Report
from app.models.user import User
from app.schemas.report import ReportCreate, ReportResponse

router = APIRouter()


@router.post(
    "",
    response_model=ReportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_report(
    body: ReportCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """다른 사용자를 신고합니다.

    - reason='other' 면 details 가 필수
    - 자기 자신 신고 금지
    - 존재하지 않는 사용자 신고 금지
    """
    if body.reported_user_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="자기 자신을 신고할 수 없습니다.",
        )
    if body.reason == "other" and not (body.details and body.details.strip()):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="기타 사유를 신고할 때는 상황 설명을 입력해주세요.",
        )

    target = await db.get(User, body.reported_user_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"user_id={body.reported_user_id} 를 찾을 수 없습니다.",
        )

    report = Report(
        reporter_id=current_user.id,
        reported_id=body.reported_user_id,
        reason=body.reason,
        details=(body.details or None) and body.details.strip() or None,
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)
    return report