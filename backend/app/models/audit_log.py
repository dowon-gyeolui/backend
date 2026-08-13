"""관리자 감사 로그 모델(AuditLog).

관리자가 한 변경을 누가·언제·무엇을·왜 했는지 남긴다. 기록은 추가만 하고
**지우지 않는다** — 삭제 경로를 만들지 않는 것이 이 테이블의 요구사항이다.

`before`/`after` 에는 PII 평문을 넣지 않는다. `services/audit.mask_sensitive` 를
거친 값만 저장하며, 그 통과는 `record_admin_action` 이 강제한다.

`admin_id` 에 FK 를 걸지 않은 것은 의도다. 관리자 계정 테이블은 아직 없고
(T-E01 이 일반 사용자와 분리된 모델로 만든다), 여기서 `users.id` 를 참조하면
그 분리를 미리 어기게 된다.
"""

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import JSON

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


_JSON = JSON().with_variant(JSONB(), "postgresql")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)

    admin_id = Column(Integer, nullable=False, index=True)
    menu = Column(String(50), nullable=False)
    target_type = Column(String(50), nullable=False)
    target_id = Column(String(64), nullable=True)  # 회원 id·주문번호 등 형식이 섞여 문자열
    action = Column(String(50), nullable=False, index=True)

    before = Column(_JSON, nullable=True)
    after = Column(_JSON, nullable=True)
    reason = Column(Text, nullable=True)
    ip = Column(String(45), nullable=True)  # IPv6 최대 45자

    created_at = Column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )

    __table_args__ = (
        # 감사 로그 화면(ADM-LOG-001)의 주 조회축: "이 대상에 대한 이력"
        Index("ix_audit_logs_target", "target_type", "target_id"),
    )
