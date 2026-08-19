"""관리자 감사 로그 조회 스키마 (ADM-LOG-001).

**여기에는 요청 스키마가 하나도 없다.** 감사 로그에 대해 관리자가 할 수 있는 일은
읽는 것뿐이고, 고치거나 지우는 경로는 만들지 않는다(QA: "로그 삭제 기능 금지").
"금지"를 주석이 아니라 **스키마의 부재**로 표현한다 — 결제 화면이 금액 수정 필드를
두지 않은 것과 같은 방식이다.

`before`/`after` 는 저장 시점에 이미 마스킹돼 있지만(`services/audit`), 이 화면은
읽을 때 한 번 더 통과시킨 값을 담는다. 마스킹 도입 전에 쌓인 행이나 서비스를 거치지
않고 들어온 행이 있어도 화면에서 원문이 나오지 않게 하려는 것이다.
"""

from datetime import datetime

from pydantic import BaseModel


class AuditActionMeta(BaseModel):
    """감사 로그에 남는 액션 하나의 사전. 목록 필터의 선택지이자 설명이다.

    화면은 라벨을 자기가 들고 있지 않고 이것을 그린다(재화·결제 화면과 같은 이유).
    """

    menu: str
    action: str
    description: str


class AuditAdmin(BaseModel):
    """로그를 남긴 관리자.

    `id=0` 은 **없는 계정으로 들어온 로그인 실패**다(`routers/admin._UNKNOWN_ADMIN_ID`).
    그때는 이름·역할이 없고, 시도된 이메일은 마스킹된 채 `after` 에 남는다.
    """

    id: int
    name: str | None
    email: str | None  # 마스킹된 값. 동명이인 관리자를 구분하는 용도다
    role: str | None
    is_active: bool | None


class AuditDiffRow(BaseModel):
    """변경 전후값 한 줄.

    `before`/`after` 가 `None` 인 것과 문자열 `"없음"` 은 다르다 — 앞은 **그 시점의
    기록에 그 항목이 없었다**는 뜻이고(예: 생성), 뒤는 값 자체가 비어 있었다는 뜻이다.
    """

    key: str
    label: str
    before: str | None
    after: str | None
    changed: bool


class AuditField(BaseModel):
    """바뀐 항목 하나의 이름. 목록이 값 없이 "무엇이 바뀌었는지"만 보여줄 때 쓴다.

    라벨을 화면이 들고 있지 않고 서버가 준다 — 새 항목이 기록되기 시작했을 때
    화면에만 컬럼명이 그대로 뜨는 일을 막는다(재화·결제 화면과 같은 규칙).
    """

    key: str
    label: str


class AuditLogRow(BaseModel):
    id: int
    created_at: datetime
    admin: AuditAdmin
    menu: str
    action: str
    target_type: str
    target_id: str | None
    reason: str | None
    ip: str | None
    # 목록에서 "무엇이 바뀌었는지"를 상세를 열지 않고 보기 위한 것. 값은 싣지 않는다.
    changed_fields: list[AuditField]


class AuditLogDetail(BaseModel):
    row: AuditLogRow
    diff: list[AuditDiffRow]


class AuditListResponse(BaseModel):
    total: int
    page: int
    size: int
    items: list[AuditLogRow]
    # 필터 선택지. 서버가 주므로 액션이 늘어도 화면이 따로 알 필요가 없다.
    menus: list[str]
    actions: list[AuditActionMeta]
    admins: list[AuditAdmin]
