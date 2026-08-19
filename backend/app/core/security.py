"""JWT access token 발급/검증 및 회원가입 비밀번호 해시/검증.

토큰 만료는 두 겹이다(OI-AUTH-002).
  - **유휴 만료(exp)**: 마지막 활동 + `idle_timeout_minutes`. 활동이 있으면
    `core/deps.py` 가 새 토큰을 내줘 시계를 다시 감고, 없으면 그대로 죽는다.
  - **절대 상한(auth_time + `access_token_expire_minutes`)**: 로그인 시각 기준.
    갱신을 아무리 반복해도 이 선을 넘으면 재로그인해야 한다.

`auth_time` 은 갱신해도 바뀌지 않는다. "마지막으로 직접 인증한 시각"이라, 민감 액션의
재인증 판정(`core/deps.py` 의 `assert_recent_auth`)이 이 값을 본다.

`scope` 는 토큰이 어느 세계의 것인지를 못 박는다. 앱 사용자와 관리자는 테이블이
분리되어 있어(DECISIONS D-4) id 공간이 겹친다 — scope 가 없으면 `sub=1` 짜리 관리자
토큰이 `users.id=1` 사용자로 통해 버린다. 두 인증 의존성은 각자 자기 scope 만 받는다.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt

from app.config import settings

ALGORITHM = "HS256"

SCOPE_USER = "user"
SCOPE_ADMIN = "admin"


@dataclass(frozen=True)
class TokenClaims:
    # 토큰 주체의 id. `scope` 에 따라 `users.id` 또는 `admin_users.id` 다.
    user_id: int
    # 이 세션이 처음 인증(로그인)된 시각. 토큰을 갱신해도 그대로 이어진다.
    auth_time: datetime
    # 이 토큰이 발급된 시각. 슬라이딩 갱신 주기를 재는 기준.
    issued_at: datetime
    # scope 클레임이 없는 토큰은 이 변경 이전에 나간 앱 사용자 토큰이다.
    # 관리자 토큰은 반드시 명시된 scope 를 갖도록 아래에서 항상 실어 보낸다.
    scope: str = SCOPE_USER

    @property
    def session_expires_at(self) -> datetime:
        """재로그인 없이 세션이 유지될 수 있는 마지막 시각(절대 상한)."""
        return self.auth_time + timedelta(minutes=settings.access_token_expire_minutes)


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))

def _create_token(subject_id: int, scope: str, auth_time: datetime | None) -> str:
    now = datetime.now(timezone.utc)
    auth_time = auth_time or now
    session_end = auth_time + timedelta(minutes=settings.access_token_expire_minutes)
    expire = min(now + timedelta(minutes=settings.idle_timeout_minutes), session_end)
    payload = {
        "sub": str(subject_id),
        "exp": expire,
        "iat": now,
        "auth_time": int(auth_time.timestamp()),
        "scope": scope,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=ALGORITHM)


def create_access_token(user_id: int, auth_time: datetime | None = None) -> str:
    """앱 사용자 토큰을 발급한다. `auth_time` 을 주면 기존 세션을 이어받는다(갱신).

    로그인 경로는 `auth_time` 없이 부른다 — 지금이 곧 인증 시각이다.
    """
    return _create_token(user_id, SCOPE_USER, auth_time)


def create_admin_access_token(admin_id: int, auth_time: datetime | None = None) -> str:
    """관리자 토큰을 발급한다. 앱 사용자 인증(`core/deps.py`)은 이 토큰을 거부한다."""
    return _create_token(admin_id, SCOPE_ADMIN, auth_time)


def _epoch_to_dt(value: object) -> datetime | None:
    if not isinstance(value, (int, float)):
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc)


def decode_token_claims(token: str) -> TokenClaims | None:
    """서명과 유휴 만료(exp)까지 검증하고 클레임을 돌려준다. 못 믿을 토큰이면 None."""
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[ALGORITHM])
    except JWTError:
        return None

    sub = payload.get("sub")
    try:
        user_id = int(sub)
    except (TypeError, ValueError):
        return None

    issued_at = _epoch_to_dt(payload.get("iat"))
    if issued_at is None:
        # 이 변경 이전에 나간 토큰에는 iat 가 없다. 그때 exp 는 "발급시각 + 절대 수명"
        # 이었으므로 거꾸로 계산해 세션 시작 시각을 복원한다. 배포하자마자 로그인된
        # 사용자를 전부 튕겨내지 않기 위한 것이고, 그 토큰들도 첫 요청에서 갱신되며
        # 유휴 만료가 붙은 새 토큰으로 바뀐다.
        exp = _epoch_to_dt(payload.get("exp"))
        if exp is None:
            return None
        issued_at = exp - timedelta(minutes=settings.access_token_expire_minutes)

    scope = payload.get("scope")
    return TokenClaims(
        user_id=user_id,
        auth_time=_epoch_to_dt(payload.get("auth_time")) or issued_at,
        issued_at=issued_at,
        scope=scope if isinstance(scope, str) else SCOPE_USER,
    )


def decode_access_token(token: str) -> int | None:
    claims = decode_token_claims(token)
    return None if claims is None else claims.user_id