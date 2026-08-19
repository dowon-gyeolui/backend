"""관리자 인증·계정 스키마."""

from datetime import datetime

from pydantic import BaseModel, Field

# 새 비밀번호 최소 길이. 부트스트랩 값(D-8)은 이보다 짧아도 되지만, 그 값은 스크립트가
# 해시로 넣는 것이라 이 검사를 거치지 않는다. 관리자가 직접 정하는 비밀번호는 회원
# 개인정보 전체를 여는 열쇠라 앱 사용자(8자)보다 길게 잡는다.
_MIN_PASSWORD_LENGTH = 10


class AdminLoginRequest(BaseModel):
    # EmailStr 를 쓰지 않는다. 형식이 틀렸을 때 422 로 답하면 "이 값은 계정 형식이
    # 아니다"라는 정보를 주게 되고, 계정 열거 방지 문구를 우회하는 신호가 된다.
    email: str = Field(max_length=255)
    password: str = Field(max_length=128)


class AdminLoginResponse(BaseModel):
    token: str
    name: str
    role: str
    must_change_password: bool
    permissions: list[str]


class AdminMeResponse(BaseModel):
    id: int
    email: str
    name: str
    role: str
    must_change_password: bool
    permissions: list[str]


class AdminPasswordChangeRequest(BaseModel):
    current_password: str = Field(max_length=128)
    new_password: str = Field(min_length=_MIN_PASSWORD_LENGTH, max_length=128)


class AdminSummary(BaseModel):
    id: int
    email: str
    name: str
    role: str
    is_active: bool
    must_change_password: bool
    last_login_at: datetime | None

    model_config = {"from_attributes": True}
