"""관리자 계정 3개 발급 (T-E00). **사람이 콘솔에서 1회 실행한다.**

루프는 운영 DB 에 쓰지 않는다(가드레일 1). 이 파일이 만드는 것은 스크립트까지이고,
실제 계정 생성은 사람이 이 스크립트를 돌려서 한다.

세 가지가 이 스크립트의 전부다.

1. **확정된 3계정을 만든다.** 이메일·이름은 2026-08-19 사용자 확정값이고, 전부
   Super Admin 이다(D-8). 값이 코드에 박혀 있는 것은 의도다 — 이건 재사용할 도구가
   아니라 한 번 쓰고 끝나는 부트스트랩이고, 인자로 받게 만들면 오타 하나가 그대로
   "회원 개인정보 전체를 여는 계정"이 된다.
2. **초기 비밀번호를 어디에도 남기지 않는다.** 코드·문서·DB·로그 어디에도 평문이
   없고, 전달 경로는 이 스크립트의 stdout 한 번뿐이다. 세 계정이 같은 값을 쓰지만
   `must_change_password=True` 라 최초 로그인에서 각자 바꿔야 하며, 바꾸기 전에는
   어떤 관리자 화면도 열리지 않는다(`core/admin_deps.require_permission`).
3. **여러 번 돌려도 안전하다.** 이미 있는 계정은 손대지 않는다. 재실행이 비밀번호를
   부트스트랩 값으로 되돌린다면, 이 스크립트를 실행할 수 있다는 것 자체가 곧 계정
   탈취 수단이 된다.

실행:

    read -rs -p "부트스트랩 비밀번호: " MELOBE_ADMIN_BOOTSTRAP_PASSWORD; echo
    export MELOBE_ADMIN_BOOTSTRAP_PASSWORD
    env -u PYTHONPATH .venv/bin/python scripts/bootstrap_admins.py
    unset MELOBE_ADMIN_BOOTSTRAP_PASSWORD

환경변수를 주지 않으면 임의 비밀번호를 만들어 출력한다. `VAR=값 명령` 형태로 주면
셸 히스토리에 평문이 남으므로 위처럼 `read -rs` 로 받는다.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from scripts._helpers import load_env_file  # noqa: E402

load_env_file(_BACKEND_ROOT)

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.core.admin_rbac import ROLE_SUPER_ADMIN  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.database import AsyncSessionLocal, init_db  # noqa: E402
from app.models.admin import AdminUser  # noqa: E402
from app.services.audit import record_admin_action  # noqa: E402

# 2026-08-19 사용자 확정. 이메일은 **소문자로만** 적는다 — 로그인이 입력을 소문자로
# 정규화하므로(`routers/admin.py`), 대문자가 섞여 저장된 계정으로는 영영 로그인할 수 없다.
ADMINS: tuple[tuple[str, str], ...] = (
    ("ympyh0312@gmail.com", "박양희"),
    ("juhyeong9072@gmail.com", "이주형"),
    ("thunderbolt9410@gmail.com", "김병철"),
)

PASSWORD_ENV = "MELOBE_ADMIN_BOOTSTRAP_PASSWORD"

_MENU = "관리자계정"
_TARGET_ADMIN = "admin"
_ACTION_CREATE = "계정생성"
_REASON = "부트스트랩 스크립트(T-E00)"

# 감사 로그의 `admin_id` 는 NOT NULL 인데, 이 스크립트를 돌리는 주체는 관리자 계정이
# 아니라 콘솔 앞의 사람이다. 로그인 실패를 남길 때와 같은 자리를 쓴다
# (`routers/admin.py` 의 `_UNKNOWN_ADMIN_ID` — 관리자 id 는 1부터 시작한다).
_SYSTEM_ADMIN_ID = 0


def resolve_password() -> str:
    """부트스트랩 비밀번호를 정한다. 환경변수가 없으면 임의로 만든다."""
    return os.environ.get(PASSWORD_ENV) or secrets.token_urlsafe(12)


async def bootstrap_admins(
    db: AsyncSession, password: str
) -> tuple[list[str], list[str]]:
    """계정을 만들고 (생성된 이메일, 이미 있어 건너뛴 이메일) 을 돌려준다.

    비밀번호 해시는 계정마다 따로 만든다. 같은 평문이라도 해시가 같으면, DB 가
    새어 나갔을 때 "이 셋은 같은 비밀번호"라는 사실까지 함께 새어 나간다.
    """
    created: list[str] = []
    skipped: list[str] = []

    for email, name in ADMINS:
        existing = (
            await db.execute(select(AdminUser).where(AdminUser.email == email))
        ).scalar_one_or_none()
        if existing is not None:
            skipped.append(email)
            continue

        admin = AdminUser(
            email=email,
            name=name,
            password_hash=hash_password(password),
            role=ROLE_SUPER_ADMIN,
            must_change_password=True,
        )
        db.add(admin)
        await db.commit()
        await db.refresh(admin)
        created.append(email)

        # 계정 생성은 "누가 이 시스템에 들어올 수 있는가"를 바꾸는 일이라 반드시 남긴다.
        # `after` 의 이메일·이름은 저장 직전에 마스킹된다(`services/audit`).
        await record_admin_action(
            admin_id=_SYSTEM_ADMIN_ID,
            menu=_MENU,
            target_type=_TARGET_ADMIN,
            target_id=admin.id,
            action=_ACTION_CREATE,
            after={"email": email, "name": name, "role": ROLE_SUPER_ADMIN},
            reason=_REASON,
        )

    return created, skipped


async def main() -> None:
    # 스키마 리비전이 코드와 어긋나면 여기서 멈춘다. `admin_users` 마이그레이션을
    # 적용하지 않은 DB 에 대고 돌려 놓고 원인을 찾아 헤매는 일을 막는다.
    await init_db()

    password = resolve_password()
    async with AsyncSessionLocal() as db:
        created, skipped = await bootstrap_admins(db, password)

    for email in skipped:
        print(f"[건너뜀] 이미 있는 계정이에요: {email}")

    if not created:
        print("새로 만든 계정이 없어요. 비밀번호도 출력하지 않아요.")
        return

    print(f"\n관리자 계정 {len(created)}개를 만들었어요 (전부 Super Admin):")
    for email in created:
        print(f"  - {email}")
    print(f"\n초기 비밀번호: {password}")
    print("이 출력이 비밀번호를 볼 수 있는 유일한 곳이에요. 지금 각자에게 전달하세요.")
    print("최초 로그인 시 비밀번호를 바꾸기 전에는 어떤 관리자 화면도 열리지 않아요.")


if __name__ == "__main__":
    asyncio.run(main())
