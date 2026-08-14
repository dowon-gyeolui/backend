"""`/knowledge/*` 접근 통제 회귀 테스트 (T-B08).

RAG 원전 코퍼스는 사주 풀이 LLM 의 grounding 이다. 적재가 열려 있으면 간접 프롬프트
인젝션이 되고, 검색이 열려 있으면 임베딩 비용을 외부인이 태울 수 있다.
"""

import pytest

_REMOVED_INGEST_PATHS = ["/knowledge/chunks", "/knowledge/ingest"]


@pytest.mark.asyncio
async def test_retrieve_requires_auth(client):
    res = await client.post("/knowledge/retrieve", json={"query": "갑목 년주"})
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_retrieve_rejects_invalid_token(client):
    res = await client.post(
        "/knowledge/retrieve",
        json={"query": "갑목 년주"},
        headers={"Authorization": "Bearer not-a-real-token"},
    )
    assert res.status_code == 401


@pytest.mark.asyncio
async def test_retrieve_allows_authenticated_user(client, make_user, auth_header):
    user = await make_user()
    res = await client.post(
        "/knowledge/retrieve",
        json={"query": "갑목 년주"},
        headers=auth_header(user),
    )
    assert res.status_code == 200
    assert isinstance(res.json(), list)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", _REMOVED_INGEST_PATHS)
async def test_http_ingest_paths_are_gone(client, path):
    """적재는 `scripts/ingest_jsonl_to_db.py` 로만 한다. 라우터로 되살리지 말 것."""
    res = await client.post(path, json={})
    assert res.status_code == 404
