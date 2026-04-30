"""Chat thread + message tables.

A ChatThread is a 1:1 conversation between two users. We canonicalise the
(user_a_id, user_b_id) pair so user_a_id < user_b_id, which means the
unique constraint guarantees there is exactly one thread per pair regardless
of who initiated it.

Message is append-only — no edit, no delete in the MVP.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ChatThread(Base):
    __tablename__ = "chat_threads"

    id = Column(Integer, primary_key=True, index=True)
    # Canonicalised pair: user_a_id < user_b_id always.
    user_a_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    user_b_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)

    # Per-user "last read message id". Used to compute unread badges.
    # 0 means the user has read nothing yet (or hasn't opened the chat).
    user_a_last_read_id = Column(Integer, default=0, nullable=False)
    user_b_last_read_id = Column(Integer, default=0, nullable=False)

    # Per-user soft-leave. KakaoTalk-style 1:1 leave: when one side leaves
    # the thread disappears from THEIR list but the other side keeps the
    # history. If they message the peer again, the leave flag is cleared
    # by `_get_or_create_thread`.
    user_a_left = Column(Boolean, default=False, nullable=False)
    user_b_left = Column(Boolean, default=False, nullable=False)

    created_at = Column(DateTime(timezone=True), default=_utcnow, nullable=False)
    # updated_at is bumped on every new message — used for thread-list ordering.
    updated_at = Column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint("user_a_id", "user_b_id", name="uq_chat_threads_pair"),
    )


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    thread_id = Column(
        Integer, ForeignKey("chat_threads.id"), nullable=False, index=True
    )
    sender_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    # 텍스트 메시지의 본문. 미디어 메시지는 빈 문자열도 허용 (혹은 캡션).
    content = Column(Text, nullable=False, default="")
    # 미디어 첨부 — Cloudinary URL. NULL 이면 순수 텍스트 메시지.
    media_url = Column(String(512), nullable=True)
    # "image" | "audio" — UI 가 렌더링 방식을 분기하기 위해.
    media_type = Column(String(16), nullable=True)
    created_at = Column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )


# Composite index supports the most common query: "give me messages of
# thread X with id > Y, ordered chronologically" (the polling pattern).
Index("ix_messages_thread_id_id", Message.thread_id, Message.id)