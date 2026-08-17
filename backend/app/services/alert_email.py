"""온보딩 완료(가입) 알림 메일 발송 서비스.

세 가지가 이 모듈의 존재 이유다.

1. **SMTP 를 쓰지 않는다.** Render 는 아웃바운드 SMTP(25/465/587)를 막는 경우가 있어
   메일이 조용히 사라진다. Resend 의 HTTP API 로 보낸다.
2. **가입을 막지 않는다.** 발송은 요청 경로 밖(백그라운드 태스크)에서 돌고, 어떤 실패도
   삼키고 로그만 남긴다. 설정(API 키·수신 주소)이 없으면 그냥 비활성화된다.
3. **개인정보를 싣지 않는다.** 본문에 넣는 것은 닉네임·회원ID·완료 시각뿐이다.
   생년월일·연락처를 메일로 보내면 메일 서버에 개인정보가 남는다.

가입자가 늘면 건별 메일이 소음이 되므로, 하루 20건까지만 건별로 보내고 그 뒤로는
20건씩 묶어 요약 한 통으로 보낸다.
"""

import asyncio
from datetime import date, datetime, timedelta, timezone

import httpx

from app.config import settings
from app.core.redact import redact_secrets

_KST = timezone(timedelta(hours=9))
_RESEND_ENDPOINT = "https://api.resend.com/emails"

# 하루 이 건수까지는 건별로 보낸다. 넘어가면 아래 크기만큼 모아 요약 한 통으로 보낸다.
_DAILY_INDIVIDUAL_LIMIT = 20
_DIGEST_SIZE = 20

# 오늘 처리한 건수와, 요약 대기 중인 줄들. 프로세스 안에서만 유지되는 상태라
# 재시작하면 카운터가 0 으로 돌아간다(그 경우 건별 메일이 몇 통 더 갈 뿐이다).
_day: date | None = None
_count = 0
_pending: list[str] = []
_lock = asyncio.Lock()

# create_task 의 반환값을 붙들지 않으면 태스크가 중간에 GC 될 수 있다.
_running: set[asyncio.Task] = set()


def _label(nickname: str | None) -> str:
    return nickname or "(닉네임 없음)"


def _digest_message(day: date | None, lines: list[str]) -> tuple[str, str]:
    day_text = day.isoformat() if day else "이전"
    return (
        f"[MeloBe] 가입 알림 요약 {len(lines)}건 ({day_text})",
        "온보딩을 완료한 회원입니다.\n\n" + "\n".join(lines),
    )


def _plan(now: datetime, user_id: int, nickname: str | None) -> list[tuple[str, str]]:
    """상태를 갱신하고 이번에 보낼 메일 목록을 만든다(발송은 하지 않는다)."""
    global _day, _count, _pending

    today = now.date()
    messages: list[tuple[str, str]] = []

    if _day != today:
        # 날이 바뀌었으면 어제 남은 대기분부터 비운다(20건이 안 차서 못 보낸 것들).
        if _pending:
            messages.append(_digest_message(_day, _pending))
        _day, _count, _pending = today, 0, []

    _count += 1
    when = now.strftime("%Y-%m-%d %H:%M KST")
    line = f"{_count}. {_label(nickname)} (회원ID {user_id}) — {when}"

    if _count <= _DAILY_INDIVIDUAL_LIMIT:
        messages.append(
            (
                f"[MeloBe] 신규 가입 — {_label(nickname)}",
                f"온보딩을 완료한 회원이 있습니다.\n\n{line}\n\n오늘 {_count}번째입니다.",
            )
        )
    else:
        _pending.append(line)
        if len(_pending) >= _DIGEST_SIZE:
            messages.append(_digest_message(today, _pending))
            _pending = []

    return messages


async def _send_email(subject: str, body: str) -> None:
    """Resend API 로 메일 한 통을 보낸다. 실패는 삼키고 로그만 남긴다."""
    if not (settings.alert_email_api_key and settings.alert_email_to):
        return

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                _RESEND_ENDPOINT,
                headers={"Authorization": f"Bearer {settings.alert_email_api_key}"},
                json={
                    "from": settings.alert_email_from,
                    "to": [settings.alert_email_to],
                    "subject": subject,
                    "text": body,
                },
            )
        if response.status_code >= 400:
            print(
                f"[alert-email] 발송 실패 status={response.status_code} "
                f"body={redact_secrets(response.text)[:200]}",
                flush=True,
            )
    except Exception as exc:
        # 메일이 안 가는 것보다 가입이 막히는 게 훨씬 나쁘다.
        print(f"[alert-email] 발송 실패: {redact_secrets(repr(exc))}", flush=True)


async def notify_onboarding_completed(user_id: int, nickname: str | None) -> None:
    """온보딩 완료 1건을 운영자에게 알린다. 예외를 밖으로 내보내지 않는다."""
    async with _lock:
        messages = _plan(datetime.now(_KST), user_id, nickname)
    for subject, body in messages:
        await _send_email(subject, body)


def schedule_onboarding_alert(user_id: int, nickname: str | None) -> None:
    """알림 발송을 백그라운드로 넘긴다(온보딩 응답을 메일 왕복만큼 늦추지 않기 위해)."""
    task = asyncio.create_task(notify_onboarding_completed(user_id, nickname))
    _running.add(task)
    task.add_done_callback(_running.discard)
