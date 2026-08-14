"""기기 토큰이 FCM 으로 통일됐는지 지키는 회귀 테스트 (T-C08).

옛 iOS 앱(@capacitor/push-notifications)은 APNs 디바이스 토큰을 올려보냈고,
백엔드는 firebase-admin 으로만 발송하므로 그 토큰으로는 아무것도 못 보냈다.
앱을 @capacitor-firebase/messaging 으로 바꿔 양 플랫폼 모두 FCM 토큰을 보내게 했으니,
DB 에 남은 옛 APNs 토큰이 (1) 발송 대상에서 빠지고 (2) 새 토큰 등록 때 정리되는지 본다.
"""

import sys
import types

import pytest
from sqlalchemy import select

from app.models.device_token import DeviceToken
from app.services.push import is_fcm_registration_token

# 실제 형식만 흉내낸 값이다(길이는 줄였다).
FCM_TOKEN = "cJ1xQ2Z3TkE:APA91bF_test_registration_token"
APNS_TOKEN = "740f4707bebcf74f9b7c25d48e3358945f6aa01da5ddb387462c7eaf61bb78ad"


def test_fcm_토큰과_apns_토큰을_형식으로_구분한다():
    assert is_fcm_registration_token(FCM_TOKEN)
    assert not is_fcm_registration_token(APNS_TOKEN)


@pytest.mark.asyncio
async def test_fcm_토큰_등록_시_옛_apns_토큰이_정리된다(client, db, make_user, auth_header):
    user = await make_user()
    db.add(DeviceToken(user_id=user.id, token=APNS_TOKEN, platform="ios"))
    await db.commit()

    res = await client.post(
        "/users/me/device-token",
        json={"token": FCM_TOKEN, "platform": "ios"},
        headers=auth_header(user),
    )
    assert res.status_code == 204

    tokens = (
        await db.execute(
            select(DeviceToken.token).where(DeviceToken.user_id == user.id)
        )
    ).scalars().all()
    assert tokens == [FCM_TOKEN]


@pytest.mark.asyncio
async def test_다른_사용자의_토큰은_건드리지_않는다(client, db, make_user, auth_header):
    """정리 범위는 등록한 사용자 본인의 토큰뿐이어야 한다."""
    me = await make_user()
    other = await make_user()
    db.add(DeviceToken(user_id=other.id, token=APNS_TOKEN, platform="ios"))
    await db.commit()

    res = await client.post(
        "/users/me/device-token",
        json={"token": FCM_TOKEN, "platform": "ios"},
        headers=auth_header(me),
    )
    assert res.status_code == 204

    remaining = (
        await db.execute(
            select(DeviceToken.token).where(DeviceToken.user_id == other.id)
        )
    ).scalars().all()
    assert remaining == [APNS_TOKEN]


@pytest.mark.asyncio
async def test_안드로이드_토큰은_그대로_유지된다(client, db, make_user, auth_header):
    """FCM 통일 과정에서 멀쩡한 안드로이드 토큰이 지워지면 안 된다."""
    user = await make_user()
    android_token = "dR9yPq4M2Lk:APA91bF_android_existing"
    db.add(DeviceToken(user_id=user.id, token=android_token, platform="android"))
    await db.commit()

    res = await client.post(
        "/users/me/device-token",
        json={"token": FCM_TOKEN, "platform": "ios"},
        headers=auth_header(user),
    )
    assert res.status_code == 204

    tokens = set(
        (
            await db.execute(
                select(DeviceToken.token).where(DeviceToken.user_id == user.id)
            )
        )
        .scalars()
        .all()
    )
    assert tokens == {android_token, FCM_TOKEN}


@pytest.mark.asyncio
async def test_apns_토큰에는_발송을_시도하지_않는다(db, make_user, monkeypatch):
    """죽은 토큰으로 messaging.send 를 부르지 않는지 확인한다."""
    from app.services import push as push_service

    user = await make_user()
    db.add(DeviceToken(user_id=user.id, token=APNS_TOKEN, platform="ios"))
    db.add(DeviceToken(user_id=user.id, token=FCM_TOKEN, platform="android"))
    await db.commit()

    sent: list[str] = []

    class _FakeMessaging:
        """send() 에 넘어간 토큰만 기록하는 firebase_admin.messaging 대역."""

        @staticmethod
        def Notification(**kwargs):
            return kwargs

        @staticmethod
        def Message(*, notification, data, token):
            return token

        @staticmethod
        def send(message, app=None):
            sent.append(message)

    fake_firebase_admin = types.ModuleType("firebase_admin")
    fake_firebase_admin.messaging = _FakeMessaging
    monkeypatch.setitem(sys.modules, "firebase_admin", fake_firebase_admin)
    # credential 이 없는 테스트 환경이라 초기화 단계를 통과시켜 준다.
    monkeypatch.setattr(push_service, "_init_firebase", lambda: object())

    await push_service.send_push_to_user(user.id, "제목", "본문", db)

    assert sent == [FCM_TOKEN]
