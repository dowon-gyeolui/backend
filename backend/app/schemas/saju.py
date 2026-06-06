from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel

class BirthInputSummary(BaseModel):

    birth_date: date
    birth_time: Optional[str] = None   
    calendar_type: str = "solar"
    is_leap_month: bool = False
    gender: Optional[str] = None


class Pillar(BaseModel):

    label: str    
    stem: str     
    branch: str   
    combined: str 

    stem_hanja: Optional[str] = None
    stem_element: Optional[str] = None      
    stem_polarity: Optional[str] = None
    stem_ten_god: Optional[str] = None
    branch_hanja: Optional[str] = None
    branch_animal: Optional[str] = None
    branch_element: Optional[str] = None
    branch_polarity: Optional[str] = None
    branch_ten_god: Optional[str] = None
    hidden_stems: list[str] = []
    twelve_stage: Optional[str] = None
    twelve_spirit: Optional[str] = None


class ElementProfile(BaseModel):

    wood: int = 0   
    fire: int = 0   
    earth: int = 0  
    metal: int = 0  
    water: int = 0

class SajuResponse(BaseModel):
    user_id: int
    input_summary: BirthInputSummary
    pillars: list[Pillar]
    element_profile: ElementProfile
    summary: str
    interpretation_status: Literal["pending", "ready"] = "pending"
    interpretation_sources: list[str] = []
    interpretation: Optional[str] = None


class DetailedSajuResponse(SajuResponse):

    personality: str = ""
    love: str = ""
    wealth: str = ""
    advice: str = ""


class TodayFortuneResponse(BaseModel):

    fortune_text: str
    today_pillar: str
    today_pillar_hanja: str
    relation: str
    element_today: str
    score: int
    headline: str = ""
    person_type: str = ""
    timing: str = ""
    place: str = ""
    caution: str = ""
    lucky_color: str = ""
    badges: list[str] = []


class ActionGuideResponse(BaseModel):

    text: str

class JamidusuPalace(BaseModel):

    name: str         
    description: str  

class JamidusuResponse(BaseModel):

    user_id: int
    overview: str = ""
    palaces: list[JamidusuPalace] = []
    main_stars_summary: str = ""
    interpretation_status: Literal["pending", "ready"] = "pending"

class JamidusuDeepStar(BaseModel):

    name: str
    name_ko: str
    type: str
    sub: Optional[str] = None

class JamidusuDeepPalace(BaseModel):

    name: str            
    name_ko: str         
    branch: str          
    branch_ko: str       
    stem: str            
    stem_ko: str         
    stars: list[JamidusuDeepStar] = []
    description: str = ""

class JamidusuDeepSections(BaseModel):

    personality: str = ""   
    love: str = ""          
    wealth: str = ""        
    advice: str = ""

class JamidusuDeepResponse(BaseModel):
    
    user_id: int
    interpretation_status: Literal["pending", "ready", "partial"] = "pending"

    bureau_name: str = ""
    year_pillar: str = ""
    lunar_birth: Optional[str] = None
    hour_assumed: bool = False

    headline: str = ""
    overview: str = ""
    sections: JamidusuDeepSections = JamidusuDeepSections()
    palaces: list[JamidusuDeepPalace] = []
    main_stars_summary: str = ""

    sources: list[str] = []
