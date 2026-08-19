"""관리자 계정 모델(AdminUser) — 일반 사용자(`users`)와 **분리된** 테이블.

분리는 취향이 아니라 결정이다(DECISIONS D-4). 같은 테이블에 플래그 하나로 관리자를
표시하면, 앱 쪽 인증 경로 어딘가에 생긴 결함이 곧바로 관리자 권한이 된다. 테이블도
토큰 scope 도 갈라 두어야 한 쪽이 뚫려도 다른 쪽으로 넘어오지 않는다
(`core/security.py` 의 `SCOPE_ADMIN`).

`must_change_password` 는 부트스트랩 비밀번호로 만든 계정이 그대로 운영에 쓰이는 것을
막는다(D-8). 이 값이 True 인 동안 관리자는 자기 정보 조회와 비밀번호 변경만 할 수 있고,
권한이 필요한 화면은 전부 막힌다(`core/admin_deps.py`).

관리자 계정은 지우지 않고 `is_active=False` 로 내린다 — 감사 로그의 `admin_id` 가
가리킬 대상이 사라지면 "누가 했는지"를 되짚을 수 없다.
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, Integer, String

from app.core.admin_rbac import ROLES
from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


_ROLE_VALUES = ", ".join(f"'{role}'" for role in ROLES)


class AdminUser(Base):
    __tablename__ = "admin_users"
    __table_args__ = (
        # 매트릭스에 없는 역할은 권한 0개로 판정되지만(닫히는 쪽 실패), 애초에 그런 값이
        # 저장되지 않는 편이 낫다. 잘못된 역할은 화면에서 "권한 없음"으로만 보여
        # 원인을 찾기 어렵다.
        CheckConstraint(f"role IN ({_ROLE_VALUES})", name="ck_admin_users_role"),
    )

    id = Column(Integer, primary_key=True, index=True)

    # 로그인 식별자. 별도 아이디를 두지 않기로 확정했다(2026-08-19).
    email = Column(String(255), unique=True, nullable=False, index=True)
    name = Column(String(50), nullable=False)
    password_hash = Column(String(255), nullable=False)

    role = Column(String(20), nullable=False)

    is_active = Column(Boolean, default=True, nullable=False)
    must_change_password = Column(Boolean, default=True, nullable=False)

    last_login_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    updated_at = Column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
