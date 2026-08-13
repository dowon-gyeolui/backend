"""관리자 감사 로그 기록.

공개 진입점은 `record_admin_action` 하나다. **삭제 함수는 두지 않는다** —
감사 로그는 추가만 하고 지우지 않는 것이 요구사항이다(ADM-LOG-001).

두 가지 성질이 이 모듈의 존재 이유다.

1. **기록 실패가 본 작업을 막지 않는다.** 그래서 호출자의 세션을 쓰지 않고
   별도 세션에서 쓰고 커밋한다. 호출자 세션에 넣었다가 insert 가 실패하면
   그 트랜잭션 전체가 못 쓰게 되어, "로그를 못 남겨서 회원 상태 변경이 실패했다"는
   본말전도가 일어난다. 대신 실패는 조용히 삼키지 않고 error 로 남긴다.
2. **before/after 에 PII 평문을 넣지 않는다.** 저장 직전에 `mask_sensitive` 를
   반드시 통과시킨다. 호출자가 마스킹을 잊어도 평문이 들어갈 수 없다.
"""

import logging
from typing import Any

from app.database import AsyncSessionLocal
from app.models.audit_log import AuditLog

logger = logging.getLogger(__name__)

_MASK = "***"

# 키 이름에 이 조각이 들어가면 값의 형태조차 남기지 않는다(부분 노출도 위험한 값들).
_SECRET_FRAGMENTS = (
    "password",
    "token",
    "secret",
    "api_key",
    "apikey",
    "credential",
    "authorization",
)

_EMAIL_KEYS = {"email", "이메일"}
_PHONE_KEYS = {"phone", "phone_number", "mobile", "tel", "전화번호", "연락처"}
_BIRTH_KEYS = {"birth_date", "birthdate", "birthday", "생년월일"}
# 형태를 남길 필요 없이 앞 한 글자만 남기는 일반 식별 정보
_PII_KEYS = {
    "name",
    "real_name",
    "이름",
    "address",
    "주소",
    "birth_place",
    "birth_time",
    "kakao_id",
    "username",
}
# `nickname` 은 일부러 빼 두었다. 앱에서 다른 사용자에게 그대로 보이는 값이고,
# 관리자 화면에서 동명이인을 구분하는 유일한 단서라 가리면 로그가 못 쓰게 된다.


def _mask_generic(value: str) -> str:
    return value[0] + "*" * (len(value) - 1) if len(value) > 1 else _MASK


def _mask_email(value: str) -> str:
    local, sep, domain = value.partition("@")
    if not sep:
        return _mask_generic(value)
    return f"{local[:2]}{_MASK}@{domain}" if len(local) > 2 else f"{_MASK}@{domain}"


def _mask_phone(value: str) -> str:
    """뒤 4자리 숫자만 남긴다. 하이픈 등 서식은 그대로 둬 형태를 알아볼 수 있게 한다."""
    kept = 0
    out = []
    for ch in reversed(value):
        if not ch.isdigit():
            out.append(ch)
        elif kept < 4:
            kept += 1
            out.append(ch)
        else:
            out.append("*")
    return "".join(reversed(out))


def _mask_birth(value: str) -> str:
    """연도만 남긴다(1995-03-20 → 1995-**-**). 나이대 확인은 되고 생일은 가려진다."""
    if len(value) < 4 or not value[:4].isdigit():
        return _mask_generic(value)
    return value[:4] + "".join("*" if c.isdigit() else c for c in value[4:])


def _mask_scalar(key: str, value: Any) -> Any:
    k = key.lower()
    if any(fragment in k for fragment in _SECRET_FRAGMENTS):
        return _MASK
    if value is None or isinstance(value, bool):
        return value

    text = value if isinstance(value, str) else str(value)
    if k in _EMAIL_KEYS:
        return _mask_email(text)
    if k in _PHONE_KEYS:
        return _mask_phone(text)
    if k in _BIRTH_KEYS:
        return _mask_birth(text)
    if k in _PII_KEYS:
        return _mask_generic(text)

    # 가릴 대상이 아니어도 JSON 컬럼에 그대로 들어갈 수 있는 타입이어야 한다.
    # date/datetime/Decimal 을 그대로 넣으면 직렬화에서 터져 기록이 통째로 날아간다.
    if isinstance(value, (str, int, float)):
        return value
    return text


def mask_sensitive(value: Any, key: str = "") -> Any:
    """dict/list 를 훑어 PII 로 알려진 키의 값을 가린다. 구조는 그대로 둔다.

    키 이름으로 판단하므로, 호출자는 컬럼명을 키로 하는 dict 를 넘겨야 한다
    (예: `{"phone": "010-1234-5678"}`). 자유 서술 문자열 안에 섞인 PII 는
    여기서 걸러지지 않는다 — 그런 값은 애초에 before/after 에 넣지 않는다.
    """
    if isinstance(value, dict):
        return {k: mask_sensitive(v, str(k)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [mask_sensitive(v, key) for v in value]
    return _mask_scalar(key, value)


async def record_admin_action(
    *,
    admin_id: int,
    menu: str,
    target_type: str,
    action: str,
    target_id: Any = None,
    before: Any = None,
    after: Any = None,
    reason: str | None = None,
    ip: str | None = None,
) -> int | None:
    """관리자 액션 한 건을 남긴다. 생성된 로그 id, 실패 시 None.

    호출자는 반환값을 확인할 필요가 없다 — 실패해도 예외를 던지지 않고,
    본 작업을 되돌리지도 않는다. 대신 실패는 error 로그로 드러난다.
    """
    try:
        log = AuditLog(
            admin_id=admin_id,
            menu=menu,
            target_type=target_type,
            target_id=None if target_id is None else str(target_id),
            action=action,
            before=mask_sensitive(before),
            after=mask_sensitive(after),
            reason=reason,
            ip=ip,
        )
        async with AsyncSessionLocal() as session:
            session.add(log)
            await session.flush()
            log_id = log.id  # commit 이 속성을 만료시키기 전에 꺼내 둔다
            await session.commit()
        return log_id
    except Exception:
        logger.exception(
            "감사 로그 기록 실패 — admin_id=%s menu=%s action=%s target=%s/%s",
            admin_id,
            menu,
            action,
            target_type,
            target_id,
        )
        return None
