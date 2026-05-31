# CLAUDE.md

ZAMI 백엔드 작업 시 행동 가이드라인.

**Tradeoff**: 신중함을 속도보다 우선한다. 오타·1줄 fix 같은 사소한 작업은 판단해서 가볍게 처리해도 된다.

---

## 일반 원칙

Andrej Karpathy 가 정리한 LLM 코딩의 흔한 실수 4가지를 막기 위한 원칙.

### 1. Think Before Coding

**가정하지 마라. 헷갈리면 숨기지 마라. 트레이드오프를 제시하라.**

- 가정은 명시적으로 말한다. 확실하지 않으면 묻는다.
- 해석이 여러 개 가능하면 다 제시한다. 조용히 하나 골라 진행하지 않는다.
- 더 단순한 방법이 있으면 그렇게 말하고, 사용자 요청도 push back 한다.
- 헷갈리면 멈춘다. 뭐가 헷갈리는지 명확히 말하고 묻는다.

### 2. Simplicity First

**문제를 푸는 최소 코드. 추측 금지.**

- 요청 외 기능 추가 금지.
- 1회용 코드를 추상화 금지.
- 요청하지 않은 "flexibility" / "configurability" 금지.
- 일어날 수 없는 시나리오를 위한 error handling 금지.
- 200줄을 짰는데 50줄로 가능하면 다시 짠다.

테스트: "시니어 엔지니어가 보고 overcomplicated 라고 할까?" → YES 면 단순화.

### 3. Surgical Changes

**필요한 것만 건드린다. 본인이 만든 흔적만 청소한다.**

기존 코드 수정 시:
- 인접 코드·주석·포맷을 "개선" 하지 않는다.
- 망가지지 않은 것 refactor 금지.
- 본인이 다르게 짤 거여도 기존 스타일을 유지한다.
- 무관한 dead code 발견 시 언급만 하고 지우지 않는다.

본인 변경이 orphan 을 만들면:
- 본인 변경이 unused 로 만든 import / 변수 / 함수만 제거.
- 사전에 있던 dead code 는 요청받기 전까지 그대로 둔다.

테스트: 바뀐 모든 줄이 사용자 요청에 직접 매핑되어야 한다.

### 4. Goal-Driven Execution

**성공 기준을 정의하라. 검증될 때까지 loop 돌려라.**

작업을 검증 가능한 목표로 변환:
- "validation 추가" → "잘못된 입력에 대한 테스트 작성 → 통과시키기"
- "버그 fix" → "재현 테스트 작성 → 통과시키기"
- "X refactor" → "before/after 테스트 모두 통과 확인"

다단 작업이면 짧은 plan 작성:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
```

---

## ZAMI 도메인 메모

일반 원칙 위에 얹는 프로젝트 특화 규칙.

### 프로젝트 정체성

사주 + 자미두수 기반 한국 데이팅 앱. 핵심은 두 가지:
1. 사주 기반 매칭 적합도 계산
2. 매칭 성공 확률을 높이는 행동·추천 제공

### Stack

- 백엔드: FastAPI(async) + SQLAlchemy 2 + asyncpg
- DB: PostgreSQL (Supabase Session pooler)
- 외부: Kakao OAuth · OpenAI(gpt-4o-mini + embedding + moderation) · AWS Rekognition · Cloudinary

### 사주 도메인은 이미 구현되어 있다 — placeholder 금지

- `services/saju_engine.py` — 정통 60갑자 엔진. 절기 / 60갑자 / 五虎遁 / 五鼠遁 / 음력 변환 정확.
- `services/jamidusu/` — 안성술 차트. 12궁 / 14주성 / 사화 결정론적.
- `services/saju_chart.py` + `saju_enrichment.py` — 천간/지지/십성/지장간/12운성/12신살/도화살/천을귀인 룩업.
- RAG 원전: 적천수천미(342) + 궁통보감(79) + 자미두수전서(59) 가 `knowledge_chunks` 에 ingest 되어 LLM 풀이의 grounding.

**새 사주/자미두수 로직을 placeholder 로 짜지 마라.** 기존 엔진을 확장하거나 룩업 테이블에 추가한다.

### 매칭 cycle

4-슬롯 daily pack, **KST 자정 00:00 anchor** (모든 사용자가 같은 unlock 시각 공유). 한 cycle = 96h.

### 보안 / 인증

- FastAPI 는 `postgres` superuser 로 DB 에 붙으므로 **Postgres RLS 가 무력화**된다. 사용자 격리는 반드시 app 레이어 (`current_user.id` 로 filter) 에서 보장.
- 탈퇴 시 hard delete + 카카오 unlink (PIPA 준수). 순서는 `services/users.delete_account` 참고.
- 채팅 메시지는 3-layer moderation (regex → 욕설 → OpenAI Moderation) 통과해야 DB 도달. 우회 금지.
- `.env` 의 시크릿이 응답·로그에 평문으로 나가지 않도록 마스킹. `app/main.py:_redact_db_url` 패턴 참고.
- 진단 엔드포인트(`/health/db` 등)는 production (`debug=False`) 에서 404 처리.

### 한국어 UI

모든 사용자 노출 텍스트는 한국어. 영문 placeholder / debug 메시지를 production 에 남기지 않는다.

### DB 마이그레이션

- Alembic 도입 전. 새 컬럼 추가 시 `database._DEV_COLUMNS` 리스트에 한 줄 추가하면 SQLite / PostgreSQL 양쪽에서 안전하게 `ALTER TABLE`.
- 새 테이블은 모델 정의 후 `app/models/__init__.py` 에 import 만 추가.

### 비용 의식

- LLM 호출은 비싸다. 응답을 클라이언트 측 stale-while-revalidate 캐시(`src/lib/cache.ts`)와 TTL 로 보호한다.
- 사진 업로드는 Rekognition → Cloudinary 순서. Rekognition 실패 시 Cloudinary 호출하지 않아 비용 절감.

---

**이 가이드라인이 작동하는지 확인하는 지표**: diff 에 요청 외 변경이 줄어든다 · overcomplication 으로 인한 재작성이 줄어든다 · 구현 전 명확화 질문이 늘어난다 (구현 후가 아니라).