"""채팅 음성 메시지 보관기간(14일) 만료 처리."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.chat import Message
from app.services.storage import delete_chat_audio_by_url

AUDIO_TTL = timedelta(days=14)


async def purge_expired_audio(db: AsyncSession) -> int:
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
            delete_chat_audio_by_url(msg.media_url)
        msg.media_url = None

    if rows:
        await db.commit()
    return len(rows)
