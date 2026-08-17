"""온보딩 완료 시 관리자 가입 알림 메일 (T-G01).

지켜야 할 성질: 완료 시 1건 · 중복 없음 · 발송이 실패해도 온보딩은 성공 ·
본문에 개인정보 없음 · 하루 20건을 넘으면 묶음 요약.
"""

import asyncio
from datetime import date, datetime, timedelta

import httpx
import pytest

from app.config import settings
from app.services import alert_email


@pytest.fixture(autouse=True)
def reset_alert_state():
    """모듈 전역 카운터가 테스트 간에 새지 않도록 초기화한다."""
    alert_email._day = None
    alert_email._count = 0
    alert_email._pending = []
    yield
    alert_email._day = None
    alert_email._count = 0
    alert_email._pending = []


@pytest.fixture
def sent(monkeypatch):
    """실제 발송 대신 (제목, 본문) 을 모으는 스텁."""
    box: list[tuple[str, str]] = []

    async def _capture(subject: str, body: str) -> None:
        box.append((subject, body))

    monkeypatch.setattr(alert_email, "_send_email", _capture)
    return box


async def _drain_background_tasks() -> None:
    """요청 핸들러가 만든 백그라운드 알림 태스크가 끝날 때까지 기다린다."""
    for _ in range(20):
        if not alert_email._running:
            return
        await asyncio.sleep(0)


async def test_온보딩_완료시_메일_한_건(client, make_user, auth_header, sent):
    user = await make_user(birth_date=None, gender=None, nickname="멜로")

    response = await client.post(
        "/users/me/birth-data",
        json={
            "birth_date": "1995-03-20",
            "birth_time": "09:30",
            "calendar_type": "solar",
            "is_leap_month": False,
            "gender": "female",
        },
        headers=auth_header(user),
    )
    assert response.status_code == 200
    await _drain_background_tasks()

    assert len(sent) == 1
    subject, body = sent[0]
    assert "멜로" in subject
    assert f"회원ID {user.id}" in body


async def test_생년월일_재전송은_중복_알림을_보내지_않는다(
    client, make_user, auth_header, sent
):
    user = await make_user(birth_date=None, gender=None)
    payload = {
        "birth_date": "1995-03-20",
        "birth_time": None,
        "calendar_type": "solar",
        "is_leap_month": False,
        "gender": "female",
    }

    for _ in range(3):
        response = await client.post(
            "/users/me/birth-data", json=payload, headers=auth_header(user)
        )
        assert response.status_code == 200
        await _drain_background_tasks()

    assert len(sent) == 1


async def test_메일_본문에_생년월일이_들어가지_않는다(
    client, make_user, auth_header, sent
):
    user = await make_user(birth_date=None, gender=None, nickname="멜로")

    await client.post(
        "/users/me/birth-data",
        json={
            "birth_date": "1995-03-20",
            "birth_time": "09:30",
            "calendar_type": "solar",
            "is_leap_month": False,
            "gender": "female",
        },
        headers=auth_header(user),
    )
    await _drain_background_tasks()

    subject, body = sent[0]
    for secret in ("1995-03-20", "1995", "09:30"):
        assert secret not in body
        assert secret not in subject


class _StubClient:
    """`alert_email` 이 만드는 httpx 클라이언트만 갈아끼우는 스텁.

    `httpx.AsyncClient.post` 자체를 갈아끼우면 테스트 HTTP 클라이언트까지 같이 죽는다
    (그 클라이언트도 httpx 다). 모듈 속성만 다른 클래스로 바꿔 원본 클래스는 남겨둔다.
    """

    calls: list[dict] = []
    error: Exception | None = None

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, **kwargs):
        _StubClient.calls.append({"url": url, **kwargs})
        if _StubClient.error:
            raise _StubClient.error
        return httpx.Response(200, json={"id": "stub"})


@pytest.fixture
def stub_http(monkeypatch):
    _StubClient.calls = []
    _StubClient.error = None
    monkeypatch.setattr(alert_email.httpx, "AsyncClient", _StubClient)
    return _StubClient


async def test_발송이_실패해도_온보딩은_성공한다(
    client, make_user, auth_header, monkeypatch, stub_http
):
    monkeypatch.setattr(settings, "alert_email_api_key", "test-alert-key")
    monkeypatch.setattr(settings, "alert_email_to", "ops@example.test")
    stub_http.error = httpx.ConnectError("메일 API 접속 실패")

    user = await make_user(birth_date=None, gender=None)
    response = await client.post(
        "/users/me/birth-data",
        json={
            "birth_date": "1995-03-20",
            "birth_time": None,
            "calendar_type": "solar",
            "is_leap_month": False,
            "gender": "female",
        },
        headers=auth_header(user),
    )

    assert response.status_code == 200
    assert response.json()["birth_date"] == "1995-03-20"
    # 예외가 태스크 밖으로 새면 여기서 터진다.
    await alert_email.notify_onboarding_completed(user.id, "멜로")


async def test_설정이_없으면_발송을_시도하지_않는다(monkeypatch, stub_http):
    monkeypatch.setattr(settings, "alert_email_api_key", "")
    monkeypatch.setattr(settings, "alert_email_to", "")

    await alert_email.notify_onboarding_completed(1, "멜로")

    assert stub_http.calls == []


async def test_설정이_있으면_Resend_API_로_보낸다(monkeypatch, stub_http):
    monkeypatch.setattr(settings, "alert_email_api_key", "test-alert-key")
    monkeypatch.setattr(settings, "alert_email_to", "ops@example.test")

    await alert_email.notify_onboarding_completed(7, "멜로")

    assert len(stub_http.calls) == 1
    call = stub_http.calls[0]
    assert call["url"] == "https://api.resend.com/emails"
    assert call["headers"]["Authorization"] == "Bearer test-alert-key"
    assert call["json"]["to"] == ["ops@example.test"]
    assert "회원ID 7" in call["json"]["text"]


async def test_하루_20건까지는_건별_그_이후는_묶음_요약(sent):
    for i in range(alert_email._DAILY_INDIVIDUAL_LIMIT):
        await alert_email.notify_onboarding_completed(i, f"회원{i}")
    assert len(sent) == 20
    assert all("신규 가입" in subject for subject, _ in sent)

    # 21~39번째는 쌓이기만 하고 메일이 나가지 않는다.
    for i in range(20, 39):
        await alert_email.notify_onboarding_completed(i, f"회원{i}")
    assert len(sent) == 20

    # 20건이 차는 40번째에서 요약 한 통.
    await alert_email.notify_onboarding_completed(39, "회원39")
    assert len(sent) == 21
    subject, body = sent[-1]
    assert "요약 20건" in subject
    assert "회원39" in body
    assert len(body.splitlines()) == 22  # 안내 1줄 + 빈 줄 1개 + 항목 20줄


async def test_날이_바뀌면_남은_대기분을_요약으로_비운다(sent, monkeypatch):
    alert_email._day = date(2026, 8, 16)
    alert_email._count = 25
    alert_email._pending = ["21. 회원A (회원ID 1) — 2026-08-16 23:50 KST"]

    await alert_email.notify_onboarding_completed(2, "회원B")

    assert len(sent) == 2
    assert "요약 1건" in sent[0][0]
    assert "2026-08-16" in sent[0][0]
    assert "신규 가입" in sent[1][0]
    # 카운터는 새 날짜로 리셋된다.
    assert alert_email._count == 1
    assert alert_email._day == datetime.now(alert_email._KST).date()


async def test_KST_기준으로_날짜를_센다():
    """UTC 로 세면 KST 09:00 이전 가입이 전날로 묶인다."""
    assert alert_email._KST.utcoffset(None) == timedelta(hours=9)
