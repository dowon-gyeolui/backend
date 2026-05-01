"""User moderation state — strike counts + chat-suspension cooldown.

We append a Strike row each time a user's chat message is auto-blocked
by chat_moderation. A short cooldown (chat_suspended_until) is applied
when the running count crosses thresholds, so repeat offenders get a
24h chat timeout without us doing manual moderation.

Photos rejected by photo_moderation don't add strikes (the user only
sees the reject message and re-uploads). We only escalate on chat
because that's where back-and-forth abuse happens.
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
)

from app.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class UserStrike(Base):
    """Append-only audit log of a single moderation block.

    `kind` matches the ChatModerationResult.kind values:
    contact_leak | profanity | harassment | sexual | spam | other.
    """

    __tablename__ = "user_strikes"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    kind = Column(String(20), nullable=False)
    # Free-form short detail for moderator triage (e.g. "phone", "openai_cat=harassment").
    detail = Column(String(120), nullable=True)
    created_at = Column(
        DateTime(timezone=True), default=_utcnow, nullable=False, index=True
    )