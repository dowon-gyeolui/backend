"""Chat-related Pydantic schemas.

Wire shapes the frontend consumes: ``MessageOut``, ``ChatThreadSummary``,
plus the bodies for sending a message and starting a thread.
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