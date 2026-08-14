"""원전 지식 청크 검색 엔드포인트.

적재(HTTP `POST /chunks`, `POST /ingest`)는 제거했다. 코퍼스에 텍스트를 심을 수 있으면
사주 풀이 LLM 의 grounding 이 오염된다(간접 프롬프트 인젝션). 적재 경로는
`scripts/ingest_jsonl_to_db.py` 하나만 남긴다.
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.database import get_db
from app.models.user import User
from app.schemas.knowledge import KnowledgeQuery, KnowledgeRetrievalResult
from app.services.knowledge import retrieval

router = APIRouter()


@router.post(
    "/retrieve",
    response_model=list[KnowledgeRetrievalResult],
    summary="원전 청크 검색",
    description=(
        "키워드와 필터로 저장된 원전 청크를 검색합니다. "
        "각 결과는 source_citation 필드로 출처 정보를 포함합니다. "
        "저장된 청크가 없으면 임시 placeholder를 반환합니다."
    ),
)
async def retrieve_knowledge(
    query: KnowledgeQuery,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return await retrieval.retrieve(query, db)
