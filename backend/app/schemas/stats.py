from typing import Optional

from pydantic import BaseModel


class GenderCount(BaseModel):
    male: int
    female: int


class SameDayStem(BaseModel):
    stem: str       # 일간(日干) 한글, 예: "갑"
    count: int      # 본인 제외, 같은 일간 회원 수


class SameElement(BaseModel):
    element: str    # 오행 한글, 예: "화"
    count: int      # 본인 제외, 같은 오행 회원 수


class HomeStats(BaseModel):
    signups_total: int
    signups_today: int
    gender: GenderCount
    active_chat_rooms: int
    today_matches: int
    active_users: int
    same_day_stem: Optional[SameDayStem] = None
    same_element: Optional[SameElement] = None
