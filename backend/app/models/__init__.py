# Import all models here so Base.metadata.create_all() sees every table.
from app.models.chat import ChatThread, Message  # noqa: F401
from app.models.daily_match import DailyMatch  # noqa: F401
from app.models.knowledge import KnowledgeChunk  # noqa: F401
from app.models.moderation import UserStrike  # noqa: F401
from app.models.photo import UserPhoto  # noqa: F401
from app.models.report import Report  # noqa: F401
from app.models.user import User  # noqa: F401