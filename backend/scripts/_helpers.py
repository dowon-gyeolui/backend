"""지식 원문 전처리 스크립트들이 공유하는 헬퍼(환경변수 로드, 헤딩 분류, 태그 추출, JSONL IO)."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional


# --- .env loading ----------------------------------------------------

def load_env_file(backend_root: Path) -> None:
    env_path = backend_root / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        print(
            f"warning: python-dotenv not installed; {env_path} was not loaded. "
            "Run: pip install -r requirements.txt",
            file=sys.stderr,
        )
        return
    load_dotenv(env_path, override=False)


_HEADING_MAX_LEN = 30

_CHAPTER_TITLES: frozenset[str] = frozenset({
    "五行總論", "十干分論", "干支總論", "四柱總論",
    "羅序", "太微賦", "形性賦", "星垣論", "斗數準繩", "斗數骨髓賦",
    "增補太微賦", "女命骨髓賦", "定富局", "定貴局",
})
_SECTION_TITLES: frozenset[str] = frozenset({
    "例曰", "干造論", "地支論", "納音論", "十二支論",
})

_CHAPTER_PATTERNS: list[re.Pattern] = [
    re.compile(r"^#\s+\S.*$"),
    re.compile(r"^第\S{1,6}[章回卷篇]\b.*$"),
    re.compile(r"^卷\S{1,6}\b.*$"),
    re.compile(r"^篇\S{1,6}\b.*$"),
    re.compile(r"^[一二三四五六七八九十百千]+、\s*\S.*$"),
    re.compile(r"^[一二三四五六七八九十]+\.\s*\S.*$"),
    re.compile(r"^論[甲乙丙丁戊己庚辛壬癸木火土金水陰陽天地]{1,3}$"),
]

_SECTION_PATTERNS: list[re.Pattern] = [
    re.compile(r"^##\s+\S.*$"),
    re.compile(r"^第\S{1,6}節\b.*$"),
    re.compile(r"^[一-鿿]{1,8}[賦歌訣序]$"),
    re.compile(r"^問[一-鿿]{2,12}$"),
    re.compile(r"^[三四][春夏秋冬][甲乙丙丁戊己庚辛壬癸]木?$"),
    re.compile(r"^[正二三四五六七八九十冬臘]月[甲乙丙丁戊己庚辛壬癸]木?$"),
    re.compile(r"^[子丑寅卯辰巳午未申酉戌亥]月[甲乙丙丁戊己庚辛壬癸]木?$"),
]


def _matches_any(patterns: list[re.Pattern], s: str) -> bool:
    return any(p.match(s) for p in patterns)


def classify_line(line: str) -> tuple[str, Optional[str]]:
    s = line.strip()
    if not s:
        return "blank", None

    if len(s) <= _HEADING_MAX_LEN:
        if s in _CHAPTER_TITLES:
            return "chapter", s
        if s in _SECTION_TITLES:
            return "section", s
        if _matches_any(_CHAPTER_PATTERNS, s):
            return "chapter", s
        if _matches_any(_SECTION_PATTERNS, s):
            return "section", s

    return "content", line.rstrip()


@dataclass
class Block:
    chapter: Optional[str]
    section: Optional[str]
    text: str


def parse_source_txt(raw_text: str) -> list[Block]:
    current_chapter: Optional[str] = None
    current_section: Optional[str] = None
    buffer: list[str] = []
    blocks: list[Block] = []

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body:
            blocks.append(Block(current_chapter, current_section, body))
        buffer.clear()

    for line in raw_text.splitlines():
        kind, value = classify_line(line)
        if kind == "chapter":
            flush()
            current_chapter = value
            current_section = None
        elif kind == "section":
            flush()
            current_section = value
        else:
            buffer.append(line)

    flush()
    return blocks


_MAX_TAGS = 12

_TAG_VOCAB: list[tuple[str, str]] = [
    ("紫微", "자미성"), ("天機", "천기성"), ("太陽", "태양성"), ("武曲", "무곡성"),
    ("天同", "천동성"), ("廉貞", "염정성"), ("天府", "천부성"), ("太陰", "태음성"),
    ("貪狼", "탐랑성"), ("巨門", "거문성"), ("天相", "천상성"), ("天梁", "천량성"),
    ("七殺", "칠살성"), ("破軍", "파군성"),
    ("左輔", "좌보"), ("右弼", "우필"), ("文昌", "문창"), ("文曲", "문곡"),
    ("天魁", "천괴"), ("天鉞", "천월"), ("祿存", "녹존"),
    ("擎羊", "경양"), ("陀羅", "타라"), ("火星", "화성"), ("鈴星", "영성"),
    ("命宮", "명궁"), ("兄弟", "형제궁"), ("夫妻", "부처궁"), ("子女", "자녀궁"),
    ("財帛", "재백궁"), ("疾厄", "질액궁"), ("遷移", "천이궁"), ("僕役", "노복궁"),
    ("官祿", "관록궁"), ("田宅", "전택궁"), ("福德", "복덕궁"), ("父母", "부모궁"),
    ("空亡", "공망"), ("廟旺", "묘왕"), ("化祿", "화록"),
    ("化權", "화권"), ("化科", "화과"), ("化忌", "화기"),
    ("正官", "정관"), ("偏官", "편관"), ("正財", "정재"), ("偏財", "편재"),
    ("正印", "정인"), ("偏印", "편인"), ("食神", "식신"), ("傷官", "상관"),
    ("比肩", "비견"), ("劫財", "겁재"),
    ("用神", "용신"), ("格局", "격국"), ("喜神", "희신"), ("忌神", "기신"),
    ("甲木", "갑목"), ("乙木", "을목"), ("丙火", "병화"), ("丁火", "정화"),
    ("戊土", "무토"), ("己土", "기토"), ("庚金", "경금"), ("辛金", "신금"),
    ("壬水", "임수"), ("癸水", "계수"),
    ("甲", "갑"), ("乙", "을"), ("丙", "병"), ("丁", "정"), ("戊", "무"),
    ("己", "기"), ("庚", "경"), ("辛", "신"), ("壬", "임"), ("癸", "계"),
    ("木", "목"), ("火", "화"), ("土", "토"), ("金", "금"), ("水", "수"),
    ("子", "자"), ("丑", "축"), ("寅", "인"), ("卯", "묘"), ("辰", "진"),
    ("巳", "사"), ("午", "오"), ("未", "미"), ("申", "신"), ("酉", "유"),
    ("戌", "술"), ("亥", "해"),
]


def extract_tags(
    text: str,
    *,
    chapter: Optional[str] = None,
    section: Optional[str] = None,
    topic: Optional[str] = None,
    extra: Optional[list[str]] = None,
    max_tags: int = _MAX_TAGS,
) -> Optional[list[str]]:
    found: set[str] = set()

    search_corpus = text or ""
    for meta in (chapter, section, topic):
        if meta:
            search_corpus += "\n" + meta

    for zh, ko in _TAG_VOCAB:
        if zh in search_corpus:
            found.add(ko)

    if extra:
        for t in extra:
            t = (t or "").strip()
            if t:
                found.add(t)

    if not found:
        return None

    return sorted(found)[:max_tags]


def load_jsonl(path: Path) -> Iterator[dict]:
    with path.open("r", encoding="utf-8") as f:
        for line_no, raw in enumerate(f, 1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                yield json.loads(raw)
            except json.JSONDecodeError as e:
                raise ValueError(f"{path}:{line_no} is not valid JSON: {e}") from e


def write_jsonl_row(fp, row: dict) -> None:
    fp.write(json.dumps(row, ensure_ascii=False) + "\n")


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
