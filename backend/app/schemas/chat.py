"""채팅 관련 Pydantic 스키마 — 프론트가 소비하는 와이어 포맷.

- MessageOut / MessageCreate: 메시지 단건 입출력
- ChatPeer: 채팅 헤더/목록에 노출되는 상대방 공개 정보
- ChatThreadSummary: 채팅 목록의 한 행 요약
"""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class MessageOut(BaseModel):
    id: int
    thread_id: int
    sender_id: int
    content: str
    media_url: Optional[str] = None
    media_type: Optional[str] = None  # "image" | "audio"
    created_at: datetime

    model_config = {"from_attributes": True}


class MessageCreate(BaseModel):
    content: str = Field(..., min_length=1, max_length=2000)


class ChatPeer(BaseModel):
    """Public-safe peer profile shown in thread list / chat header."""

    user_id: int
    nickname: Optional[str] = None
    photo_url: Optional[str] = None


class ChatThreadSummary(BaseModel):
    """One row in the chat thread list."""

    thread_id: int
    peer: ChatPeer
    last_message: Optional[MessageOut] = None
    updated_at: datetime
    # Number of messages in this thread the current user hasn't seen yet.
    # 0 means everything is read. Used to render the unread badge.
    unread_count: int = 0