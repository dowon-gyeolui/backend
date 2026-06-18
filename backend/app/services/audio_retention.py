"""채팅 음성 메시지 보관기간 만료 처리.

음성은 전송 후 14일까지만 열람 가능하고, 이후 폐기된다:
  - DB: 해당 메시지의 media_url 을 비워 더 이상 재생되지 않게 한다.
    (말풍선은 남아 프론트에서 '만료된 음성메시지' 로 표시.)
  - Cloudinary: 실제 음성 파일을 best-effort 로 삭제한다.

main.py 의 lifespan 백그라운드 루프가 주기적으로 purge_expired_audio 를 호출한다.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import Message
from app.services.storage import delete_chat_audio_by_url

# 음성 열람 가능 기간.
AUDIO_TTL = timedelta(days=14)


async def purge_expired_audio(db: AsyncSession) -> int:
    """14일 지난 음성 메시지를 폐기. 폐기한 건수를 반환."""
    cutoff = datetime.now(timezone.utc) - AUDIO_TTL
    rows = (
        await db.execute(
            select(Message)
            .where(Message.media_type == "audio")
            .where(Message.media_url.is_not(None))
            .where(Message.created_at < cutoff)
        )
    ).scalars().all()

    for msg in rows:
        if msg.media_url:
            delete_chat_audio_by_url(msg.media_url)  # best-effort Cloudinary 삭제
        msg.media_url = None

    if rows:
        await db.commit()
    return len(rows)
