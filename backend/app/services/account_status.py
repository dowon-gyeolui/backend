"""회원 상태(비활성·차단)를 매칭 밖의 경로에도 적용하는 판정 한 벌 (T-E10).

T-E03 이 `users.status`(정상/비활성/차단)와 `users.profile_hidden` 을 만들었지만
적용한 곳은 매칭 후보 풀뿐이었다. 그래서 관리자가 내린 회원이 여전히 로그인하고,
채팅을 보내고, 이미 열람된 카드의 공개 프로필에 노출됐다. 이 모듈이 그 나머지
경로가 쓰는 유일한 판정이다 — 라우터마다 `status != "active"` 를 따로 적으면
한 곳이 반드시 뒤처지고, 뒤처진 그 한 곳이 곧 정지 회원의 우회로가 된다.

**본인이 막히는 것과 상대가 안 보이는 것은 다른 판정이다.**
- 본인: 상태가 정상이 아니면 앱을 쓸 수 없다(`assert_usable`). 403 이지 401 이 아니다 —
  세션은 멀쩡하고 계정이 막힌 것이라, 401 이면 클라이언트가 토큰을 버리고 로그인
  화면으로 보내는데 거기서도 막혀 사용자는 이유를 영영 못 본다.
- 상대: 정지된 회원은 남에게 보이지 않는다(`assert_peer_usable`). 여기에 노출 중단
  (`profile_hidden`)까지 더 보는 것은 공개 프로필뿐이다(`assert_peer_profile_visible`) —
  노출 중단은 "프로필만 내린 상태"라 이미 열린 채팅까지 끊을 근거가 되지 않는다.
"""

from fastapi import HTTPException, status as http_status

from app.models.user import STATUS_ACTIVE, STATUS_BLOCKED, STATUS_INACTIVE, User

# 계정 상태 때문에 막혔음을 클라이언트가 "권한 없음"과 구분하는 표식.
# (`X-Reauth-Required` 와 같은 역할 — 같은 403 이라도 사용자에게 보일 화면이 다르다.)
ACCOUNT_STATUS_HEADER = "X-Account-Status"

# ⚠️ 잠정 문구다. 차단된 회원이 앱에서 무엇을 보는지는 아직 확정된 바가 없어
# (T-E10 백로그) `state/NEEDS_HUMAN.md` 에 확정 요청을 올려 뒀다.
_SELF_DETAIL = {
    STATUS_INACTIVE: "계정이 비활성 상태예요. 고객센터로 문의해주세요.",
    STATUS_BLOCKED: "이용이 제한된 계정이에요. 고객센터로 문의해주세요.",
}
_SELF_DETAIL_FALLBACK = "지금은 계정을 이용할 수 없어요. 고객센터로 문의해주세요."

# 상대의 상태를 사유별로 나눠 알려주지 않는다. "정지된 회원"과 "프로필을 내린 회원"을
# 구분해 주면 그 자체가 남의 계정 상태를 캐는 조회 수단이 된다.
_PEER_UNAVAILABLE_DETAIL = "지금은 이용할 수 없는 상대예요."
_PEER_PROFILE_DETAIL = "지금은 볼 수 없는 프로필이에요."


def is_usable(user: User) -> bool:
    """이 회원이 앱을 쓸 수 있는 상태인가."""
    return user.status == STATUS_ACTIVE


def assert_usable(user: User) -> None:
    """본인 확인 — 상태가 정상이 아니면 403. 토큰 발급과 인증 의존성이 쓴다."""
    if is_usable(user):
        return
    raise HTTPException(
        status_code=http_status.HTTP_403_FORBIDDEN,
        detail=_SELF_DETAIL.get(user.status, _SELF_DETAIL_FALLBACK),
        headers={ACCOUNT_STATUS_HEADER: user.status},
    )


def assert_peer_usable(peer: User) -> None:
    """상대 확인 — 정지된 회원과는 주고받지 않는다. 채팅 전송·궁합 조회가 쓴다."""
    if is_usable(peer):
        return
    raise HTTPException(
        status_code=http_status.HTTP_403_FORBIDDEN,
        detail=_PEER_UNAVAILABLE_DETAIL,
    )


def assert_peer_profile_visible(peer: User) -> None:
    """공개 프로필 노출 확인 — 상태와 노출 중단을 함께 본다."""
    if is_usable(peer) and not peer.profile_hidden:
        return
    raise HTTPException(
        status_code=http_status.HTTP_403_FORBIDDEN,
        detail=_PEER_PROFILE_DETAIL,
    )
