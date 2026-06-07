"""ORM 모델 일괄 등록 — Base.metadata.create_all() 이 모든 테이블을
인식하도록 모든 모델을 이 모듈에서 import 한다.
"""

# Import all models here so Base.metadata.create_all() sees every table.
from app.models.card_unlock import CardUnlock  # noqa: F401
from app.models.chat import ChatThread, Message  # noqa: F401
from app.models.daily_match import DailyMatch  # noqa: F401
from app.models.knowledge import KnowledgeChunk  # noqa: F401
from app.models.moderation import UserStrike  # noqa: F401
from app.models.payment import StarOrder  # noqa: F401
from app.models.photo import UserPhoto  # noqa: F401
from app.models.report import Report  # noqa: F401
from app.models.user import User  # noqa: F401