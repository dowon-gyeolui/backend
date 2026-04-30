"""Chat endpoints — REST + polling.

The frontend identifies a conversation by the OTHER user's id (peer_id).
That keeps URLs like /matching/{peer_id} stable on the client side; the
server resolves to a canonical ChatThread row internally.

Polling pattern:
    GET /chat/with/{peer_id}/messages?after_id=<last_seen_id>
returns only messages with id > after_id, so the client can append new
ones cheaply every couple of seconds.
"""

from typing import Literal, Optional

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from sqlalchemy import and_, delete, desc, func, or_, select
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
from app.services.storage import (
    StorageNotConfiguredError,
    upload_chat_audio,
    upload_chat_image,
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
        # If the current user previously left this thread, sending a new
        # message brings it back. Reset their leave flag so the row
        # reappears in /chat/threads.
        if current_user_id == existing.user_a_id and existing.user_a_left:
            existing.user_a_left = False
        elif current_user_id == existing.user_b_id and existing.user_b_left:
            existing.user_b_left = False
        return existing

    thread = ChatThread(user_a_id=small, user_b_id=large)
    db.add(thread)
    await db.commit()
    await db.refresh(thread)
    return thread


def _peer_id_of(thread: ChatThread, current_user_id: int) -> int:
    return thread.user_b_id if thread.user_a_id == current_user_id else thread.user_a_id


def _my_left_flag(thread: ChatThread, current_user_id: int) -> bool:
    return (
        thread.user_a_left
        if thread.user_a_id == current_user_id
        else thread.user_b_left
    )


def _my_last_read_id(thread: ChatThread, current_user_id: int) -> int:
    return (
        thread.user_a_last_read_id
        if thread.user_a_id == current_user_id
        else thread.user_b_last_read_id
    )


@router.get("/threads", response_model=list[ChatThreadSummary])
async def list_threads(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List my chat threads, newest first. Each row carries the peer's
    public-safe profile, the last message (if any), and the unread count.

    Threads where the current user has flagged left=True are excluded;
    the OTHER user keeps seeing the thread until they leave too.
    """
    # SQL-level filter for the user's leave flag — saves us from
    # post-filtering and from joining when the user has many threads.
    left_filter = or_(
        and_(
            ChatThread.user_a_id == current_user.id,
            ChatThread.user_a_left.is_(False),
        ),
        and_(
            ChatThread.user_b_id == current_user.id,
            ChatThread.user_b_left.is_(False),
        ),
    )

    rows = (
        await db.execute(
            select(ChatThread)
            .where(left_filter)
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

        last_read = _my_last_read_id(thread, current_user.id)
        unread_count = (
            await db.execute(
                select(func.count(Message.id)).where(
                    and_(
                        Message.thread_id == thread.id,
                        Message.id > last_read,
                        Message.sender_id != current_user.id,
                    )
                )
            )
        ).scalar_one() or 0

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
                unread_count=int(unread_count),
            )
        )
    return summaries


@router.post(
    "/with/{peer_id}/read",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def mark_thread_read(
    peer_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Mark all messages in this thread as read by the current user.

    Called by the chat room on mount and whenever a poll yields new
    messages — sets `*_last_read_id` to the latest Message.id so the
    unread badge in /chat/threads drops to 0.
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
        # Nothing to mark — the thread will be created on first message send.
        return None

    latest_id = (
        await db.execute(
            select(func.max(Message.id)).where(Message.thread_id == thread.id)
        )
    ).scalar_one() or 0

    if thread.user_a_id == current_user.id:
        if latest_id > thread.user_a_last_read_id:
            thread.user_a_last_read_id = int(latest_id)
    else:
        if latest_id > thread.user_b_last_read_id:
            thread.user_b_last_read_id = int(latest_id)
    await db.commit()
    return None


@router.delete(
    "/threads/{thread_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def leave_thread(
    thread_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Leave a chat thread (KakaoTalk-style 1:1 leave).

    Sets the current user's `*_left` flag — the row disappears from
    their /chat/threads list. The OTHER user keeps the conversation.
    If the OTHER user had already left, the thread + its messages are
    hard-deleted since nobody's listening anymore.
    """
    thread = await db.get(ChatThread, thread_id)
    if thread is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"thread_id={thread_id} 를 찾을 수 없습니다.",
        )
    if current_user.id not in (thread.user_a_id, thread.user_b_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="이 채팅방의 참여자가 아닙니다.",
        )

    if current_user.id == thread.user_a_id:
        thread.user_a_left = True
        peer_left = bool(thread.user_b_left)
    else:
        thread.user_b_left = True
        peer_left = bool(thread.user_a_left)

    if peer_left:
        # Both sides have left — orphan thread, hard-delete it + messages.
        await db.execute(delete(Message).where(Message.thread_id == thread.id))
        await db.delete(thread)

    await db.commit()
    return None


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


_MAX_MEDIA_BYTES = 12 * 1024 * 1024  # 12 MB

_ALLOWED_IMAGE_TYPES = {
    "image/jpeg", "image/png", "image/webp", "image/heic", "image/heif", "image/gif",
}
_ALLOWED_AUDIO_TYPES = {
    "audio/webm", "audio/mp4", "audio/mpeg", "audio/wav",
    "audio/x-m4a", "audio/m4a", "audio/aac", "audio/ogg",
}


@router.post(
    "/with/{peer_id}/messages/media",
    response_model=MessageOut,
    status_code=status.HTTP_201_CREATED,
)
async def send_media_message(
    peer_id: int,
    media_type: Literal["image", "audio"] = Form(...),
    file: UploadFile = File(...),
    caption: Optional[str] = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """이미지/오디오 첨부 메시지. 채팅의 + 버튼 메뉴(사진/카메라/음성)가 호출."""
    target = await db.get(User, peer_id)
    if target is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"user_id={peer_id} not found",
        )

    if media_type == "image":
        if file.content_type not in _ALLOWED_IMAGE_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"이미지 형식만 업로드 가능합니다 (받은: {file.content_type}).",
            )
    elif media_type == "audio":
        if file.content_type not in _ALLOWED_AUDIO_TYPES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"오디오 형식만 업로드 가능합니다 (받은: {file.content_type}).",
            )

    raw = await file.read()
    if len(raw) > _MAX_MEDIA_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"파일이 너무 큽니다. {_MAX_MEDIA_BYTES // (1024 * 1024)}MB 이하로 보내주세요.",
        )

    try:
        if media_type == "image":
            url = upload_chat_image(raw, sender_id=current_user.id)
        else:
            url = upload_chat_audio(raw, sender_id=current_user.id)
    except StorageNotConfiguredError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(e),
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"미디어 업로드 실패: {e}",
        ) from e

    thread = await _get_or_create_thread(current_user.id, peer_id, db)
    msg = Message(
        thread_id=thread.id,
        sender_id=current_user.id,
        content=(caption or "").strip(),
        media_url=url,
        media_type=media_type,
    )
    db.add(msg)
    thread.updated_at = msg.created_at or thread.updated_at
    await db.commit()
    await db.refresh(msg)
    return MessageOut.model_validate(msg)