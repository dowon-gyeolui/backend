"""Chat endpoints — REST + polling.

The frontend identifies a conversation by the OTHER user's id (peer_id).
That keeps URLs like /matching/{peer_id} stable on the client side; the
server resolves to a canonical ChatThread row internally.

Polling pattern:
    GET /chat/with/{peer_id}/messages?after_id=<last_seen_id>
returns only messages with id > after_id, so the client can append new
ones cheaply every couple of seconds.
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.database import get_db
from app.models.chat import ChatThread, Message
from app.models.user import User
from app.schemas.chat import (
    ChatPeer,
    ChatThreadSummary,
    MessageCreate,
    MessageOut,
)

router = APIRouter()


def _canonical_pair(a: int, b: int) -> tuple[int, int]:
    """Return (small, large) so the unique constraint catches duplicates
    regardless of which user initiated the thread."""
    return (a, b) if a < b else (b, a)


async def _get_or_create_thread(
    current_user_id: int,
    peer_id: int,
    db: AsyncSession,
) -> ChatThread:
    if current_user_id == peer_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="자기 자신과는 채팅을 시작할 수 없습니다.",
        )

    small, large = _canonical_pair(current_user_id, peer_id)
    existing = (
        await db.execute(
            select(ChatThread).where(
                and_(
                    ChatThread.user_a_id == small,
                    ChatThread.user_b_id == large,
                )
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    thread = ChatThread(user_a_id=small, user_b_id=large)
    db.add(thread)
    await db.commit()
    await db.refresh(thread)
    return thread


def _peer_id_of(thread: ChatThread, current_user_id: int) -> int:
    return thread.user_b_id if thread.user_a_id == current_user_id else thread.user_a_id


@router.get("/threads", response_model=list[ChatThreadSummary])
async def list_threads(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List my chat threads, newest first. Each row carries the peer's
    public-safe profile and the last message (if any)."""
    rows = (
        await db.execute(
            select(ChatThread)
            .where(
                or_(
                    ChatThread.user_a_id == current_user.id,
                    ChatThread.user_b_id == current_user.id,
                )
            )
            .order_by(desc(ChatThread.updated_at))
        )
    ).scalars().all()

    summaries: list[ChatThreadSummary] = []
    for thread in rows:
        peer_id = _peer_id_of(thread, current_user.id)
        peer = await db.get(User, peer_id)
        last_msg = (
            await db.execute(
                select(Message)
                .where(Message.thread_id == thread.id)
                .order_by(desc(Message.id))
                .limit(1)
            )
        ).scalar_one_or_none()

        summaries.append(
            ChatThreadSummary(
                thread_id=thread.id,
                peer=ChatPeer(
                    user_id=peer.id if peer else peer_id,
                    nickname=peer.nickname if peer else None,
                    photo_url=peer.photo_url if peer else None,
                ),
                last_message=MessageOut.model_validate(last_msg)
                if last_msg
                else None,
                updated_at=thread.updated_at,
            )
        )
    return summaries


@router.get("/with/{peer_id}/messages", response_model=list[MessageOut])
async def get_messages_with_peer(
    peer_id: int,
    after_id: Optional[int] = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return messages of the thread between me and `peer_id`.

    If no thread exists yet (no messages have been sent), returns an empty
    list — the client doesn't need to special-case the first message.

    `after_id`: when set, return only messages with id > after_id (poll mode).
    """
    if peer_id == current_user.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="자기 자신과는 채팅할 수 없습니다.",
        )

    small, large = _canonical_pair(current_user.id, peer_id)
    thread = (
        await db.execute(
            select(ChatThread).where(
                and_(
                    ChatThread.user_a_id == small,
                    ChatThread.user_b_id == large,
                )
            )
        )
    ).scalar_one_or_none()
    if thread is None:
        return []

    stmt = select(Message).where(Message.thread_id == thread.id)
    if after_id is not None:
        stmt = stmt.where(Message.id > after_id)
    stmt = stmt.order_by(Message.id).limit(min(limit, 500))

    msgs = (await db.execute(stmt)).scalars().all()
    return [MessageOut.model_validate(m) for m in msgs]


@router.post(
    "/with/{peer_id}/messages",
    response_model=MessageOut,
    status_code=status.HTTP_201_CREATED,
)
async def send_message_to_peer(
    peer_id: int,
    body: MessageCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Send a message to peer. Creates the thread on first send."""
    target = await db.get(User, peer_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"user_id={peer_id} not found",
        )

    thread = await _get_or_create_thread(current_user.id, peer_id, db)

    msg = Message(
        thread_id=thread.id,
        sender_id=current_user.id,
        content=body.content,
    )
    db.add(msg)
    # Bump the thread's updated_at so it floats to the top of the thread list.
    thread.updated_at = msg.created_at or thread.updated_at
    await db.commit()
    await db.refresh(msg)
    return MessageOut.model_validate(msg)