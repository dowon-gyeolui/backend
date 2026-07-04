"""일별 AI 텍스트(운세/행동가이드) 캐시 조회 및 생성."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.daily_ai_text import DailyAiText
from app.models.user import User
from app.services.llm.interpret import generate_daily_text

_KST = timezone(timedelta(hours=9))

async def _find(db: AsyncSession, user_id: int, day, kind: str) -> Optional[DailyAiText]:
    return (
        await db.execute(
            select(DailyAiText)
            .where(DailyAiText.user_id == user_id)
            .where(DailyAiText.kst_date == day)
            .where(DailyAiText.kind == kind)
        )
    ).scalar_one_or_none()

async def get_or_create_daily_text(
    user: User,
    kind: str,
    signal_text: str,
    db: AsyncSession,
) -> Optional[str]:
    today = datetime.now(_KST).date()

    row = await _find(db, user.id, today, kind)
    if row is not None:
        return row.text

    nickname = (user.nickname or "").strip() or "고객"
    text = await asyncio.to_thread(
        generate_daily_text, kind=kind, nickname=nickname, signal_text=signal_text
    )
    if not text:
        return None

    db.add(DailyAiText(user_id=user.id, kst_date=today, kind=kind, text=text))
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        row = await _find(db, user.id, today, kind)
        return row.text if row else text
    return text
