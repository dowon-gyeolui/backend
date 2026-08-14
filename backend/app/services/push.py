"""FCM 푸시 알림 발송 서비스.

Credential 은 운영(Render) 환경을 우선 고려해 `settings.firebase_service_account_json`
(JSON 문자열 env var) 을 먼저 보고, 없으면 로컬 개발 편의로 리포 루트의
`firebase-service-account.json` 파일을 폴백으로 쓴다. 둘 다 없으면 푸시는
조용히 비활성화되고(Sentry 연동과 동일한 패턴) 앱 기동에는 영향 없다.
"""

import asyncio
import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.device_token import DeviceToken

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_LOCAL_CREDENTIAL_PATH = _REPO_ROOT / "firebase-service-account.json"

_firebase_app = None
_init_attempted = False


def is_fcm_registration_token(token: str) -> bool:
    """FCM 등록 토큰 형식인지 판별한다.

    iOS 앱이 예전에 쓰던 @capacitor/push-notifications 는 **APNs 디바이스 토큰**을
    올려보냈다. 이 서비스는 firebase-admin 의 messaging.send() 로만 발송하므로 그 토큰으로는
    아무것도 보낼 수 없다(iOS 푸시 100% 실패 — T-C08). 앱은 이제 양 플랫폼 모두
    FCM 등록 토큰을 보내지만, DB 에 남은 옛 APNs 토큰은 여전히 걸러내야 한다.

    형식 차이가 분명해서 문자열만으로 구분된다:
      FCM  `cJ1x...:APA91bF...`  — 콜론으로 나뉜 두 부분
      APNs `740f4707bebcf74f...` — 콜론 없는 16진수
    """
    return ":" in token


def _init_firebase():
    global _firebase_app, _init_attempted
    if _init_attempted:
        return _firebase_app
    _init_attempted = True
    try:
        import firebase_admin
        from firebase_admin import credentials

        if settings.firebase_service_account_json:
            cred = credentials.Certificate(
                json.loads(settings.firebase_service_account_json)
            )
        elif _LOCAL_CREDENTIAL_PATH.exists():
            cred = credentials.Certificate(str(_LOCAL_CREDENTIAL_PATH))
        else:
            print("[push] Firebase credential 없음 — 푸시 비활성화", flush=True)
            return None

        _firebase_app = firebase_admin.initialize_app(cred)
    except Exception as exc:
        print(f"[push] Firebase 초기화 실패 — 푸시 비활성화: {exc!r}", flush=True)
        _firebase_app = None
    return _firebase_app


async def send_push_to_user(
    user_id: int,
    title: str,
    body: str,
    db: AsyncSession,
    data: dict[str, str] | None = None,
) -> None:
    """user_id 의 모든 기기 토큰에 푸시를 보낸다. 실패해도 예외를 삼켜 호출부에 영향 없다.

    data 는 알림을 탭했을 때 앱이 어느 화면으로 갈지 판단하는 데 쓴다(FCM 규격상 값은 문자열).
    """
    try:
        app = _init_firebase()
        if app is None:
            return

        from firebase_admin import messaging

        tokens = (
            await db.execute(
                select(DeviceToken.token).where(DeviceToken.user_id == user_id)
            )
        ).scalars().all()

        for token in tokens:
            # 옛 APNs 토큰은 보내봐야 확정 실패다. 삭제는 여기서 하지 않는다 —
            # 이 함수는 호출부의 세션에 쓰기를 하지 않는다는 약속을 지킨다.
            # 정리는 앱이 새 FCM 토큰을 등록할 때 POST /users/me/device-token 이 한다.
            if not is_fcm_registration_token(token):
                print(
                    f"[push] FCM 토큰이 아니라 건너뜀(옛 APNs 토큰) user_id={user_id}",
                    flush=True,
                )
                continue
            try:
                # messaging.send 는 동기 HTTP 호출이라 그대로 부르면 이벤트 루프가 막힌다.
                await asyncio.to_thread(
                    messaging.send,
                    messaging.Message(
                        notification=messaging.Notification(title=title, body=body),
                        data=data or {},
                        token=token,
                    ),
                    app=app,
                )
            except Exception as exc:
                print(f"[push] 발송 실패 user_id={user_id}: {exc!r}", flush=True)
    except Exception as exc:
        print(f"[push] send_push_to_user 실패 user_id={user_id}: {exc!r}", flush=True)


async def send_daily_match_push(db: AsyncSession) -> int:
    """자정 새 인연 카드 안내를 기기 토큰이 등록된 모든 사용자에게 보낸다.

    반환값은 발송 대상 사용자 수. 개별 발송 실패는 send_push_to_user 가 삼킨다.
    """
    user_ids = (
        await db.execute(select(DeviceToken.user_id).distinct())
    ).scalars().all()

    for user_id in user_ids:
        await send_push_to_user(
            user_id,
            "오늘의 새로운 인연",
            "자정이 지나 새 인연 카드가 도착했어요",
            db,
            # 채팅 푸시와 달리 홈(오늘의 인연)으로 보낸다.
            {"route": "/home"},
        )
    return len(user_ids)
