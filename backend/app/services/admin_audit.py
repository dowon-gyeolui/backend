"""관리자 감사 로그 조회 (ADM-LOG-001).

이 모듈이 지키는 세 가지.

1. **읽기만 한다.** 감사 로그를 지우거나 고치는 함수를 두지 않는다. append-only 는
   DB 권한으로도 강제돼 있고(`docs/db-roles.md`: `audit_logs` 에 UPDATE/DELETE 를
   주지 않는다), 여기서 편의로 여는 순간 그 설계가 무의미해진다.
   `tests/test_admin_audit.py::test_no_mutation_path_exists` 가 이름 수준에서 막는다.
2. **읽을 때 다시 마스킹한다.** 저장 시점 마스킹(`services/audit`)이 1차 방어이고
   이건 2차다. 마스킹이 없던 시절의 행이나 서비스를 우회해 들어온 행이 있어도
   화면에는 원문이 나오지 않는다. 두 번 마스킹해도 값이 되돌아오지 않는다.
3. **액션 사전을 한 곳에만 둔다.** `ACTION_CATALOG` 가 "무엇이 기록되는가"의 유일한
   목록이고, 화면의 필터 선택지도 여기서 나온다. 라우터가 남기는 액션과 이 목록이
   어긋나면 테스트가 깨진다(`test_catalog_matches_recorded_actions`) — 그것이
   완료 조건의 "중요 액션 누락 여부 확인"이다.
"""

import json
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.admin import AdminUser
from app.models.audit_log import AuditLog
from app.schemas.admin_audit import (
    AuditActionMeta,
    AuditAdmin,
    AuditDiffRow,
    AuditField,
    AuditListResponse,
    AuditLogDetail,
    AuditLogRow,
)
from app.services.audit import mask_sensitive

_KST = timezone(timedelta(hours=9))

# 없는 계정으로 들어온 로그인 실패가 쓰는 admin_id (`routers/admin._UNKNOWN_ADMIN_ID`).
UNKNOWN_ADMIN_ID = 0

MENU_ADMIN_AUTH = "관리자인증"
MENU_ADMIN_ACCOUNT = "관리자계정"
MENU_MEMBER = "회원"
MENU_MATCHING = "매칭"
MENU_PAYMENT = "결제"
MENU_WALLET = "재화"

# 지금 코드가 실제로 남기는 액션 전부. B-2(감사 로그 대상, OI-LOG-001 확정)의 항목과
# 라우터의 `record_admin_action` 호출을 잇는 자리다.
# **여기 없는 액션이 기록되거나, 여기 있는데 아무도 기록하지 않으면 테스트가 깨진다.**
ACTION_CATALOG: tuple[AuditActionMeta, ...] = (
    AuditActionMeta(
        menu=MENU_ADMIN_AUTH,
        action="로그인",
        description="관리자가 로그인했어요. 계정 도용 추적의 출발점이에요.",
    ),
    AuditActionMeta(
        menu=MENU_ADMIN_AUTH,
        action="로그인실패",
        description="로그인이 거절됐어요. 없는 계정으로 시도하면 관리자 번호가 0으로 남아요.",
    ),
    AuditActionMeta(
        menu=MENU_ADMIN_AUTH,
        action="로그아웃",
        description="관리자가 로그아웃했어요. 언제 자리를 떠났는지가 남아요.",
    ),
    AuditActionMeta(
        menu=MENU_ADMIN_ACCOUNT,
        action="비밀번호변경",
        description="관리자가 자기 비밀번호를 바꿨어요. 비밀번호 값은 남기지 않아요.",
    ),
    AuditActionMeta(
        menu=MENU_MEMBER,
        action="상태변경",
        description="회원 상태를 정상·비활성·차단 사이에서 바꿨어요.",
    ),
    AuditActionMeta(
        menu=MENU_MEMBER,
        action="프로필노출중단",
        description="회원 프로필을 매칭에서 내렸어요. 사용자에게 체감되는 제재예요.",
    ),
    AuditActionMeta(
        menu=MENU_MEMBER,
        action="프로필노출재개",
        description="내렸던 프로필을 다시 노출했어요.",
    ),
    AuditActionMeta(
        menu=MENU_MEMBER,
        action="민감정보열람",
        description="회원 민감정보 원문을 봤어요. 본 항목만 남고 본 값은 남지 않아요.",
    ),
    AuditActionMeta(
        menu=MENU_MATCHING,
        action="후보재계산",
        description="매칭 후보를 다시 판정했어요. 비용이 드는 작업이라 남용을 추적해요.",
    ),
    AuditActionMeta(
        menu=MENU_PAYMENT,
        action="지급재처리",
        description="결제 건의 재화 지급을 다시 시도했어요. 이미 지급된 경우에도 남아요.",
    ),
    AuditActionMeta(
        menu=MENU_WALLET,
        action="지급",
        description="운영 보상으로 스타를 지급했어요.",
    ),
    AuditActionMeta(
        menu=MENU_WALLET,
        action="회수",
        description="스타를 회수했어요.",
    ),
)

# B-2 가 기록 대상으로 정했지만 **그 기능이 아직 없어서** 남길 것이 없는 항목.
# 목록을 비워 두면 "빠졌는데 아무도 모르는" 상태와 구분되지 않는다. 기능이 생기면
# 여기서 `ACTION_CATALOG` 로 옮겨야 하고, 옮기지 않으면 테스트가 알려 준다.
PENDING_ACTIONS: tuple[tuple[str, str], ...] = (
    ("회원 정보 수정", "회원 정보를 고치는 화면이 아직 없어요 (T-E03 은 상태·노출까지)."),
    ("매칭 카드 비활성화·수동 매칭", "해당 기능이 아직 없어요 (T-E05·OI-SCOPE-002)."),
    ("CSV 등 내보내기", "내보내기 기능이 아직 없어요 (OI-OPS-002 미확정)."),
)

# before/after 에 들어오는 키의 한국어 이름. **모르는 키는 키 이름 그대로 보여준다** —
# 빈칸으로 덮으면 무엇이 바뀌었는지 알 수 없는 줄이 조용히 생긴다(재화 화면과 같은 규칙).
FIELD_LABELS: dict[str, str] = {
    "status": "회원 상태",
    "profile_hidden": "프로필 노출 중단",
    "star_balance": "스타 잔액",
    "amount": "수량",
    "applied": "반영 여부",
    "request_id": "요청 식별자",
    "granted": "지급 여부",
    "credited": "지급 여부(이전)",
    "credited_stars": "지급 스타",
    "user_id": "회원 번호",
    "email": "이메일",
    "fields": "열람 항목",
    "state": "매칭 상태",
    "pool_count": "후보 풀",
    "available_count": "노출 가능 후보",
}

MENUS: tuple[str, ...] = tuple(
    dict.fromkeys(meta.menu for meta in ACTION_CATALOG)
)


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite 는 tz 를 떨어뜨려 돌려준다. 저장은 항상 UTC 이므로 되붙인다."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=timezone.utc)


def _kst_day_bounds(
    day_from: date | None, day_to: date | None
) -> tuple[datetime | None, datetime | None]:
    """KST 달력 날짜를 UTC 구간으로. 관리자가 말하는 "8월 19일"은 한국 날짜다."""
    start = (
        None
        if day_from is None
        else datetime.combine(day_from, datetime.min.time(), _KST).astimezone(
            timezone.utc
        )
    )
    end = (
        None
        if day_to is None
        else datetime.combine(
            day_to + timedelta(days=1), datetime.min.time(), _KST
        ).astimezone(timezone.utc)
    )
    return start, end


# --- 마스킹 · 표시 ---------------------------------------------------------------


def masked_payload(value: Any) -> dict[str, Any] | None:
    """저장된 before/after 를 화면에 낼 수 있는 dict 로.

    dict 가 아닌 값(과거 호출자가 스칼라·리스트를 넣었을 수 있다)은 `값` 하나짜리
    dict 로 감싼다. 통째로 버리면 그 기록이 화면에서 사라진다.
    """
    if value is None:
        return None
    masked = mask_sensitive(value)
    return masked if isinstance(masked, dict) else {"값": masked}


def display(value: Any) -> str:
    """표시용 문자열. 로그는 읽히라고 남기는 것이라 코드값 그대로 두지 않는다."""
    if value is None:
        return "(없음)"
    if isinstance(value, bool):
        return "예" if value else "아니오"
    if isinstance(value, (list, tuple)):
        return ", ".join(display(item) for item in value) if value else "(없음)"
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def field_label(key: str) -> str:
    return FIELD_LABELS.get(key, key)


def diff_rows(before: Any, after: Any) -> list[AuditDiffRow]:
    """변경 전후값 diff. 키 순서는 before → after 의 등장 순서다.

    한쪽에만 있는 키도 한 줄로 낸다. 교집합만 비교하면 "새로 생긴 값"과 "사라진 값"이
    화면에서 사라지는데, 그 두 가지가 감사에서 가장 눈에 띄어야 할 변화다.
    """
    left = masked_payload(before) or {}
    right = masked_payload(after) or {}
    keys = list(dict.fromkeys([*left.keys(), *right.keys()]))
    rows: list[AuditDiffRow] = []
    for key in keys:
        has_left, has_right = key in left, key in right
        rows.append(
            AuditDiffRow(
                key=key,
                label=field_label(key),
                before=display(left[key]) if has_left else None,
                after=display(right[key]) if has_right else None,
                changed=not (has_left and has_right and left[key] == right[key]),
            )
        )
    return rows


def changed_fields(before: Any, after: Any) -> list[AuditField]:
    return [
        AuditField(key=row.key, label=row.label)
        for row in diff_rows(before, after)
        if row.changed
    ]


# --- 조회 -----------------------------------------------------------------------


def _admin(admin: AdminUser | None, admin_id: int) -> AuditAdmin:
    if admin is None:
        return AuditAdmin(id=admin_id, name=None, email=None, role=None, is_active=None)
    return AuditAdmin(
        id=admin.id,
        name=admin.name,
        # 관리자 이메일도 마스킹해 내린다. 동명이인을 구분할 만큼은 남고, 감사 화면이
        # 관리자 계정 목록을 대신하는 통로가 되지는 않는다.
        email=mask_sensitive({"email": admin.email})["email"],
        role=admin.role,
        is_active=admin.is_active,
    )


def _row(log: AuditLog, admin: AdminUser | None) -> AuditLogRow:
    return AuditLogRow(
        id=log.id,
        created_at=_as_utc(log.created_at),
        admin=_admin(admin, log.admin_id),
        menu=log.menu,
        action=log.action,
        target_type=log.target_type,
        target_id=log.target_id,
        reason=log.reason,
        ip=log.ip,
        changed_fields=changed_fields(log.before, log.after),
    )


def build_list_query(
    *,
    q: str | None,
    menu: str | None,
    action: str | None,
    admin_id: int | None,
    target_type: str | None,
    target_id: str | None,
    day_from: date | None,
    day_to: date | None,
):
    """목록 조회 조건. 최신순 고정은 호출부가 아니라 여기서 정한다.

    관리자 계정은 outer join 이다 — `admin_id=0`(없는 계정으로 온 로그인 실패)이나
    나중에 계정이 지워진 경우에도 **그 로그가 목록에서 사라지면 안 된다.**
    """
    stmt = select(AuditLog, AdminUser).outerjoin(
        AdminUser, AdminUser.id == AuditLog.admin_id
    )
    if q:
        term = q.strip()
        if term:
            like = f"%{term.lower()}%"
            stmt = stmt.where(
                or_(
                    func.lower(func.coalesce(AuditLog.target_id, "")).like(like),
                    func.lower(func.coalesce(AuditLog.reason, "")).like(like),
                    func.lower(func.coalesce(AdminUser.name, "")).like(like),
                )
            )
    if menu:
        stmt = stmt.where(AuditLog.menu == menu)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if admin_id is not None:
        stmt = stmt.where(AuditLog.admin_id == admin_id)
    if target_type:
        stmt = stmt.where(AuditLog.target_type == target_type)
    if target_id:
        stmt = stmt.where(AuditLog.target_id == str(target_id))

    start, end = _kst_day_bounds(day_from, day_to)
    if start is not None:
        stmt = stmt.where(AuditLog.created_at >= start)
    if end is not None:
        stmt = stmt.where(AuditLog.created_at < end)

    return stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())


async def list_logs(
    db: AsyncSession,
    *,
    q: str | None = None,
    menu: str | None = None,
    action: str | None = None,
    admin_id: int | None = None,
    target_type: str | None = None,
    target_id: str | None = None,
    day_from: date | None = None,
    day_to: date | None = None,
    page: int = 1,
    size: int = 20,
) -> AuditListResponse:
    stmt = build_list_query(
        q=q,
        menu=menu,
        action=action,
        admin_id=admin_id,
        target_type=target_type,
        target_id=target_id,
        day_from=day_from,
        day_to=day_to,
    )
    total = await db.scalar(
        select(func.count()).select_from(stmt.order_by(None).subquery())
    )
    rows = await db.execute(stmt.offset((page - 1) * size).limit(size))
    return AuditListResponse(
        total=int(total or 0),
        page=page,
        size=size,
        items=[_row(log, admin) for log, admin in rows.all()],
        menus=list(MENUS),
        actions=list(ACTION_CATALOG),
        admins=await list_admin_options(db),
    )


async def list_admin_options(db: AsyncSession) -> list[AuditAdmin]:
    """"누가" 필터의 선택지. 관리자 계정 표는 작아 전부 준다."""
    rows = await db.execute(select(AdminUser).order_by(AdminUser.name, AdminUser.id))
    return [_admin(admin, admin.id) for admin in rows.scalars()]


async def get_log(db: AsyncSession, log_id: int) -> AuditLogDetail | None:
    row = (
        await db.execute(
            select(AuditLog, AdminUser)
            .outerjoin(AdminUser, AdminUser.id == AuditLog.admin_id)
            .where(AuditLog.id == log_id)
        )
    ).first()
    if row is None:
        return None
    log, admin = row
    return AuditLogDetail(row=_row(log, admin), diff=diff_rows(log.before, log.after))
