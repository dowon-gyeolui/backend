"""요청 단위 컨텍스트: 현재 로그인 사용자 id 를 DB 레이어까지 전달한다.

RLS 정책이 보는 세션 변수(`app.current_user_id`)를 채우려면 "지금 열린 이 트랜잭션이
누구의 것인가"를 SQLAlchemy 이벤트 훅이 알아야 한다. 훅은 Request 를 받을 수 없으므로
contextvar 로 넘긴다. 값을 넣는 곳은 인증 의존성(`core/deps.py`), 읽는 곳은
`database.py` 의 `after_begin` 훅이다. (docs/db-roles.md §6.1)
"""

from contextvars import ContextVar

from starlette.types import ASGIApp, Receive, Scope, Send

# None = 인증되지 않은 컨텍스트(로그인 전 요청, 백그라운드 루프). 이때 세션 변수는
# 빈 문자열로 걸리고 RLS 헬퍼 `app_current_user_id()` 가 NULL 을 돌려줘 아무 행도 안 보인다.
current_user_id: ContextVar[int | None] = ContextVar("current_user_id", default=None)


class CurrentUserContextMiddleware:
    """요청 시작마다 사용자 컨텍스트를 비우고, 끝나면 원래대로 되돌린다.

    uvicorn 은 요청마다 새 task 를 만들고 task 는 컨텍스트를 복사하므로 사실상 요청 간
    격리가 되지만, **남의 데이터가 보이는 실패**를 서버 구현 세부사항에 맡기지 않는다.
    task 를 새로 만들지 않는 호출자(테스트의 ASGITransport 등)에서도 값이 새지 않아야 한다.

    BaseHTTPMiddleware 를 쓰지 않는 이유: 그쪽은 다운스트림을 별도 task 로 돌려서
    엔드포인트가 넣은 값이 여기로 돌아오지 않는다. 순수 ASGI 미들웨어는 같은 task 라
    reset 이 실제로 의미를 갖는다.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        token = current_user_id.set(None)
        try:
            await self.app(scope, receive, send)
        finally:
            current_user_id.reset(token)
