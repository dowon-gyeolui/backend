"""홈 화면 통계(가입자 수, 채팅방 수 등) 응답 스키마."""

from typing import Optional

from pydantic import BaseModel


class GenderCount(BaseModel):
    male: int
    female: int


class SameDayStem(BaseModel):
    stem: str
    count: int


class SameElement(BaseModel):
    element: str
    count: int


class HomeStats(BaseModel):
    signups_total: int
    signups_today: int
    gender: GenderCount
    active_chat_rooms: int
    today_matches: int
    active_users: int
    same_day_stem: Optional[SameDayStem] = None
    same_element: Optional[SameElement] = None
