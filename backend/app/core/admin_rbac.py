"""관리자 권한 매트릭스 — Super Admin / Viewer 2단계 (OI-AUTH-003 확정).

**판정은 서버에서만 한다.** 관리자 앱이 버튼을 숨기는 것은 편의일 뿐이고, URL 을 직접
두드려도 같은 답이 나와야 한다(QA 체크포인트: RBAC). 그래서 이 모듈은 라우터가 아니라
의존성(`core/admin_deps.require_permission`)에서 쓰이며, 화면이 새로 생겨도 권한 판정
코드가 화면마다 복사되지 않는다.

규칙은 한 줄로 요약된다: **조회는 둘 다, 변경은 Super Admin 만.**
예외가 하나 있다 — `MEMBER_UNMASK` 는 조회인데도 Super Admin 전용이다. 민감정보 원문
열람은 Viewer 에게 마스킹을 유지하도록 정해져 있다(OI-MEM-004).

여기 나열된 권한 중 일부는 아직 붙일 화면이 없다(T-E03~T-E08 이 붙인다). 매트릭스를
화면마다 나눠 정의하면 "이 역할이 무엇을 할 수 있는가"를 한눈에 볼 곳이 없어지고,
권한 하나가 조용히 빠져도 아무도 모른다. 목록은 여기 한 곳에만 둔다.
"""

ROLE_SUPER_ADMIN = "super_admin"
ROLE_VIEWER = "viewer"

ROLES = (ROLE_SUPER_ADMIN, ROLE_VIEWER)

# 권한 이름은 `<메뉴>:<동작>`. 메뉴 이름은 QA 기능정의서 02/03 의 관리자 메뉴를 따른다.
MEMBER_READ = "member:read"
MEMBER_WRITE = "member:write"
MEMBER_UNMASK = "member:unmask"
MATCH_READ = "match:read"
MATCH_WRITE = "match:write"
PAYMENT_READ = "payment:read"
PAYMENT_WRITE = "payment:write"
WALLET_READ = "wallet:read"
WALLET_WRITE = "wallet:write"
AUDIT_READ = "audit:read"
ADMIN_READ = "admin:read"

# Viewer 가 가진 전부. 조회만 있고, 어떤 상태도 바꾸지 못한다.
_VIEWER_PERMISSIONS = frozenset(
    {MEMBER_READ, MATCH_READ, PAYMENT_READ, WALLET_READ}
)

# Super Admin 에게만 있는 것. 변경 권한 + 조회이지만 민감한 둘(원문 열람·감사 로그·
# 관리자 계정 목록).
_SUPER_ONLY_PERMISSIONS = frozenset(
    {
        MEMBER_WRITE,
        MEMBER_UNMASK,
        MATCH_WRITE,
        PAYMENT_WRITE,
        WALLET_WRITE,
        AUDIT_READ,
        ADMIN_READ,
    }
)

PERMISSIONS_BY_ROLE: dict[str, frozenset[str]] = {
    ROLE_SUPER_ADMIN: _VIEWER_PERMISSIONS | _SUPER_ONLY_PERMISSIONS,
    ROLE_VIEWER: _VIEWER_PERMISSIONS,
}

ALL_PERMISSIONS = _VIEWER_PERMISSIONS | _SUPER_ONLY_PERMISSIONS


def permissions_for(role: str) -> frozenset[str]:
    """역할이 가진 권한 집합. 모르는 역할은 **아무 권한도 없다**.

    빈 집합을 돌려주는 것이 핵심이다. 오타난 역할이나 나중에 추가될 역할이 기본으로
    무언가를 할 수 있게 되면, 매트릭스를 고치는 것을 잊은 순간이 곧 권한 상승이 된다.
    """
    return PERMISSIONS_BY_ROLE.get(role, frozenset())


def has_permission(role: str, permission: str) -> bool:
    return permission in permissions_for(role)
