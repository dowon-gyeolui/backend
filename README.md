# ⚙️ ZAMI 백엔드

> FastAPI 기반 사주/자미두수 매칭 + 채팅 + 모더레이션 백엔드

[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688)](https://fastapi.tiangolo.com)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB)](https://python.org)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15-336791)](https://postgresql.org)
[![SQLAlchemy](https://img.shields.io/badge/SQLAlchemy-2.0-D71F00)](https://sqlalchemy.org)

---

## 📋 프로젝트 개요

| 항목 | 내용 |
|---|---|
| **역할** | 사주 계산, 매칭, 채팅, 모더레이션, OAuth, 결제 (예정) |
| **배포** | Render (api.thezami.io) |
| **DB (프로덕션)** | PostgreSQL |
| **DB (로컬)** | SQLite |

---

## 🛠 기술 스택

### 프레임워크 / 핵심
- **FastAPI 0.115.0** + Uvicorn 0.30.6
- **Pydantic 2.9.2** + pydantic-settings
- **SQLAlchemy 2.0.36** (async)
- `asyncpg` (PostgreSQL), `aiosqlite` (SQLite)

### 외부 통합
- `httpx` — 카카오 API
- `openai` — LLM 풀이 + 채팅 모더레이션
- `boto3` — AWS Rekognition
- `cloudinary` — 이미지 호스팅
- `python-jose` — JWT
- `korean-lunar-calendar` — 음력 변환
- `python-multipart` — 파일 업로드

---

## 📁 폴더 구조

```
backend/app
├── main.py              # 앱 부팅, CORS, 라우터, init_db
├── config.py            # 환경변수 (Pydantic Settings)
├── database.py          # SQLAlchemy 세션 + dev 마이그레이션
│
├── core/
│   └── deps.py          # get_current_user (JWT 검증)
│
├── models/              # SQLAlchemy ORM
├── schemas/             # Pydantic 응답/요청
├── routers/             # HTTP 라우트
├── services/            # 비즈니스 로직
├── data/
│   ├── raw/             # 원전 텍스트 (궁통보감, 자미두수전서, 적천수천미)
│   └── processed/       # JSONL + 한글 번역
└── scripts/             # 인제스트, 시드, 마이그레이션 스크립트
```

### 📊 models — SQLAlchemy ORM

| 파일 | 모델 |
|---|---|
| `user.py` | User |
| `chat.py` | ChatThread, Message |
| `photo.py` | UserPhoto (다중 사진 갤러리) |
| `daily_match.py` | DailyMatch (4-슬롯 사이클) |
| `moderation.py` | UserStrike (위반 기록) |
| `report.py` | Report (사용자 신고) |
| `knowledge.py` | KnowledgeChunk (RAG) |

### 🔌 routers — HTTP 라우트

| 파일 | 주요 엔드포인트 |
|---|---|
| `auth.py` | `/auth/kakao`, `/auth/kakao/callback` |
| `users.py` | `/users/me`, `/users/me/photos`, `/users/{id}/public-profile` |
| `saju.py` | `/saju/me`, `/saju/me/detailed`, `/saju/me/jamidusu` |
| `compatibility.py` | `/compatibility/today`, `/history`, `/destiny`, `/date-recommendation` |
| `chat.py` | `/chat/threads`, `/chat/with/{peer}/messages`, `/unread-summary` |
| `recommendations.py` | `/recommendations/me`, `/pair/{id}` |
| `reports.py` | `/reports` |
| `knowledge.py` | `/knowledge/chunks`, `/ingest`, `/retrieve` |

### 🧠 services — 비즈니스 로직

| 파일 | 역할 |
|---|---|
| `auth.py` | 카카오 OAuth (token 교환, 프로필, unlink) |
| `users.py` | User CRUD + 탈퇴 시 FK 정리 + public_profile |
| `photos.py` | 다중 사진 갤러리 (add/delete/set_primary) |
| `photo_moderation.py` | 🛡 AWS Rekognition (얼굴/NSFW) |
| `chat_moderation.py` | 🛡 채팅 3-레이어 (정규식/욕설/OpenAI Moderation) |
| `compatibility.py` | 사주 호환성 + 매칭 + daily 사이클 + history |
| `saju.py` | 사주 계산 메인 진입점 |
| `saju_chart.py` | 명식 룩업 (천간/지지/십성/지장간/12운성/12신살) |
| `saju_engine.py` | 절기 + 일간/월간/년간/시간 계산 엔진 |
| `recommendations.py` | 페어 추천 LLM |
| `storage.py` | Cloudinary 업로드/삭제 |
| `knowledge/` | RAG (chunking, embedding, retrieval, ingestion) |
| `llm/interpret.py` | OpenAI 기반 사주/페어/자미두수/데이트 풀이 |

### 📜 scripts

```bash
build_knowledge_jsonl.py    # 원전 → JSONL
ingest_jsonl_to_db.py       # JSONL → DB embedding
translate_chunks.py         # chunk 한글 번역
seed_demo_users.py          # 데모 사용자 생성
unblind_all_users.py        # 모든 사용자 is_paid=True (테스트용)
validate_jsonl.py           # JSONL 스키마 검증
```

---

## 🗄 데이터 모델

### `users`
```
id, kakao_id (unique), nickname, photo_url
birth_date, birth_time, calendar_type, is_leap_month, gender, birth_place
bio, height_cm, mbti, job, region, smoking, drinking, religion
is_paid                       # 프리미엄 여부
chat_suspended_until          # 모더레이션 정지 만료 시각
created_at, updated_at
```

### `user_photos`
```
id, user_id (FK), url, public_id (Cloudinary)
position (정렬), is_primary (메인 사진 여부)
created_at
```

### `chat_threads`
```
id, user_a_id, user_b_id  # canonical small/large
user_a_last_read_id, user_b_last_read_id  # unread 계산용
user_a_left, user_b_left  # 소프트 leave 플래그
created_at, updated_at    # 마지막 메시지 시각으로 자동 갱신
```

### `messages`
```
id, thread_id, sender_id
content, media_url, media_type (image|audio)
created_at
```

### `daily_matches`
```
id, user_id, candidate_id, slot_index (0-3), assigned_at
# 한 묶음(=cycle)은 같은 assigned_at 공유 — KST 정오 12:00 anchor
```

### `user_strikes`
```
id, user_id, kind (contact_leak|profanity|harassment|sexual|spam|other), detail
created_at
# 24h 내 누적 3회 → chat_suspended_until 세팅
```

### `reports`, `knowledge_chunks`
```
reports:           id, reporter_id, reported_id, reason, details, created_at
knowledge_chunks:  id, source_title, source_author, topic, chapter
                   content_original (한문), content_korean (번역)
                   embedding (vector), citation, content_hash
```

---

## 🔌 주요 API 엔드포인트

### 🔐 인증
| Method | Path | 설명 |
|---|---|---|
| GET | `/auth/kakao` | 카카오 동의 페이지로 redirect |
| GET | `/auth/kakao/callback` | code → JWT 발급 → 프론트 redirect |

### 👤 사용자
| Method | Path | 설명 |
|---|---|---|
| GET | `/users/me` | 내 프로필 |
| PATCH | `/users/me/profile` | 닉네임/사진 외 (bio, mbti 등) |
| POST | `/users/me/birth-data` | 생년월일 등 필수 정보 |
| PATCH | `/users/me/birth-data` | 부분 수정 |
| POST | `/users/me/photo` | 레거시 단일 사진 업로드 |
| GET | `/users/me/photos` | 갤러리 사진 리스트 |
| POST | `/users/me/photos` | 갤러리에 추가 (최대 6장) |
| DELETE | `/users/me/photos/{id}` | 사진 삭제 (Cloudinary 정리) |
| PATCH | `/users/me/photos/{id}/primary` | 메인 사진 지정 |
| POST | `/users/me/upgrade-demo` | is_paid 데모 토글 |
| DELETE | `/users/me` | 탈퇴 (FK 정리 + 카카오 unlink) |
| GET | `/users/{id}/public-profile` | 상대 공개 프로필 |

### 🔮 사주
| Method | Path | 설명 |
|---|---|---|
| GET | `/saju/me` | 4기둥 + 오행 + 짧은 해석 |
| GET | `/saju/me/detailed` | + 4섹션 LLM 풀이 |
| GET | `/saju/me/jamidusu` | 자미두수 12궁 + 14주성 (프리미엄) |

### 💕 매칭
| Method | Path | 설명 |
|---|---|---|
| GET | `/compatibility/score/{id}` | 두 사용자 호환성 점수 |
| GET | `/compatibility/matches?top_k=N` | 탑-N 후보 (레거시) |
| GET | `/compatibility/today` | 오늘의 4-슬롯 카드 팩 |
| GET | `/compatibility/history` | 누적 매칭 히스토리 (dedup) |
| GET | `/compatibility/report/{id}` | 채팅 헤더 운명 분석 |
| GET | `/compatibility/destiny/{id}` | 운명의 실타래 5섹션 LLM |
| GET | `/compatibility/date-recommendation/{id}` | 데이트 장소 4-5개 |

### 💬 채팅
| Method | Path | 설명 |
|---|---|---|
| GET | `/chat/threads` | thread 리스트 (unread 포함) |
| DELETE | `/chat/threads/{id}` | 소프트 leave |
| GET | `/chat/unread-summary` | nav 빨간 점용 합계 |
| POST | `/chat/with/{peer}/read` | mark read |
| GET | `/chat/with/{peer}/messages?after_id=N` | 메시지 폴링 |
| POST | `/chat/with/{peer}/messages` | 텍스트 전송 |
| POST | `/chat/with/{peer}/messages/media` | 이미지/음성 전송 |

---

## ⭐ 핵심 기능

### 🔐 1. 카카오 OAuth
- `services/auth.py` — token 교환, 프로필 조회, **unlink**
- 탈퇴 시 `unlink_kakao_user(kakao_id)` 자동 호출
- `KAKAO_ADMIN_KEY` 사용 → access_token 없이도 unlink 가능

### 🔮 2. 사주 계산
- `services/saju_engine.py` — 절기 기준 일/월/년/시간
- `services/saju_chart.py` — 천간/지지/십성/지장간/12운성/12신살 룩업
- `services/saju.py` — 메인 진입점, 캐시 친화적
- 음력 변환 (`korean-lunar-calendar`)
- 출생지 기반 지역시 보정

### 📚 3. RAG (Retrieval-Augmented Generation)
- **원전**: 궁통보감 (여춘태), 자미두수전서, 적천수천미
- JSONL chunk + 한글 번역
- embedding 유사도 검색
- LLM 풀이 시 관련 원전 구절 grounding

### 🤖 4. LLM 사용
- **모델**: `gpt-4o-mini` (cost-optimized)
- **모더레이션**: `omni-moderation-latest` (한국어, 무료)

| 함수 | 용도 |
|---|---|
| `generate_saju_interpretation` | 단순 사주 풀이 |
| `generate_detailed_interpretation` | 4섹션 (성격/연애/재물/조언) |
| `generate_jamidusu_interpretation` | 자미두수 12궁 |
| `generate_destiny_analysis` | 페어 5섹션 (운명의 실타래) |
| `generate_date_recommendation` | 데이트 장소 |
| `generate_pair_recommendation` | 페어 추천 |

### 💕 5. 4-슬롯 매칭

**🕛 KST 정오 12:00 anchor** — 모든 사용자가 같은 사이클 시각 공유

```
한 cycle = 96시간 (4일)
├── slot 0 (사주, 무료)      → 즉시 unlock
├── slot 1 (자미두수, 유료)  → 즉시 unlock (결제 시 사진 공개)
├── slot 2 (사주, 무료)      → assigned_at + 72h 후 unlock
└── slot 3 (자미두수, 유료)  → assigned_at + 72h 후 unlock
```

- `GET /today` — 현재 4장, 96h 지나면 새 사이클 자동 생성
- `GET /history` — 누적 노출된 모든 후보 (candidate_id dedup)

### 📷 6. 다중 사진 갤러리
- 사용자별 최대 **6장**
- `is_primary` 플래그로 메인 1장 선택
- 삭제 시 Cloudinary asset도 destroy
- 메인 삭제 시 다른 사진 자동 승격

### 🛡 7. 사진 모더레이션 (AWS Rekognition)
| 검사 | 거절 조건 |
|---|---|
| `DetectFaces` | 얼굴 0개 / 2개 이상 / 면적 8% 미만 / 신뢰도 90% 미만 |
| `DetectModerationLabels` | NSFW / 폭력 카테고리 |

> Cloudinary 업로드 **이전**에 호출 → 실패 시 비용 절감

### 🛡 8. 채팅 모더레이션 (3-레이어)
| Layer | 방식 | 잡는 것 | 시간 |
|---|---|---|---|
| **1** | 정규식 | 휴대폰/카톡ID/SNS/URL leak | ~0.5ms |
| **2** | 욕설 사전 | 한국어 + 영어 욕설 + 변형 | ~0.1ms |
| **3** | OpenAI Moderation | 괴롭힘/위협/성적 컨텍스트 | ~200ms |

> 위반 시 `user_strikes` 기록 → 24h 내 3회 → `chat_suspended_until` 24h

### 🖼 9. Cloudinary 사진 처리
- **EXIF 회전 적용** (`angle: exif`) → 안드로이드 사진 깨짐 방지
- **HEIC → JPG** (`format: jpg`) → iOS 호환
- 800×800 limit + quality auto → 사이즈 절감

### 🔄 10. 자동 마이그레이션
```python
# database.py
init_db()
├── Base.metadata.create_all      # 신규 테이블
└── _dev_migrate_*                # 누락 컬럼 자동 ADD COLUMN IF NOT EXISTS
```

> Alembic 도입 전까지의 임시 방편

---

## 🌐 외부 통합

### ☁️ Cloudinary
- 프로필 사진 + 갤러리 + 채팅 미디어
- EXIF 자동 회전 + JPG 변환

### 🤖 OpenAI
- `gpt-4o-mini` — 사주 LLM 풀이
- `omni-moderation-latest` — 채팅 모더레이션 (한국어, 무료)
- `text-embedding-3-small` — RAG 검색

### 🛡 AWS Rekognition
- `DetectFaces` (얼굴 검증)
- `DetectModerationLabels` (NSFW)
- region: `ap-northeast-2` (서울)

### 💬 Kakao
- OAuth 2.0 로그인
- `/v1/user/unlink` (탈퇴 시 동의 해제)

---

## 🔑 환경변수

```bash
# Database
DATABASE_URL=postgresql+asyncpg://user:pass@host/db  # 또는 SQLite (기본)

# Kakao
KAKAO_CLIENT_ID=...                  # REST API 키
KAKAO_CLIENT_SECRET=...              # client secret (옵션)
KAKAO_REDIRECT_URI=https://api.thezami.io/auth/kakao/callback
KAKAO_ADMIN_KEY=...                  # 탈퇴 시 unlink 용 어드민 키

# Auth
SECRET_KEY=...                       # JWT 서명
ACCESS_TOKEN_EXPIRE_MINUTES=10080    # 7일

# CORS / OAuth redirect (콤마 구분, 첫 항목 = redirect 타겟)
FRONTEND_URLS=https://thezami.io,https://www.thezami.io,http://localhost:3000

# OpenAI
OPENAI_API_KEY=sk-...
OPENAI_INTERPRET_MODEL=gpt-4o-mini   # 옵션

# Cloudinary
CLOUDINARY_URL=cloudinary://api_key:api_secret@cloud_name
# 또는 분리:
# CLOUDINARY_CLOUD_NAME=...
# CLOUDINARY_API_KEY=...
# CLOUDINARY_API_SECRET=...

# AWS Rekognition (사진 모더레이션)
AWS_ACCESS_KEY_ID=AKIA...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=ap-northeast-2

# Misc
DEBUG=true                           # SQLAlchemy echo
```

---

## 🚀 빌드 / 배포

| 항목 | 값 |
|---|---|
| **GitHub** | `dowon-gyeolui/backend` (main 브랜치) |
| **CI/CD** | Render 자동 배포 (main 푸시 시 트리거) |
| **도메인** | api.thezami.io (가비아 DNS CNAME → Render) |

### 부팅 시 자동 작업
```
1. 모든 모델 import 등록 (app/models/__init__.py)
2. Base.metadata.create_all (신규 테이블 생성)
3. _dev_migrate (누락된 컬럼 자동 추가)
```

---

## 💻 개발 워크플로우

### 로컬 실행
```bash
cd backend
pip install -r requirements.txt
cp .env.example .env  # 환경변수 채우기
uvicorn app.main:app --reload --port 8000
```

### 데이터 인제스트
```bash
python scripts/build_knowledge_jsonl.py    # 원전 → JSONL
python scripts/translate_chunks.py         # 한글 번역
python scripts/ingest_jsonl_to_db.py       # embedding + DB 저장
python scripts/seed_demo_users.py          # 데모 사용자 생성
```

---

## 🔒 보안 / 컴플라이언스

### 탈퇴 시 처리 순서 (`services/users.delete_account`)
```
1. 채팅 thread + 메시지 정리
2. 본인 메시지 (sender_id) 정리
3. 사진 + Cloudinary asset 정리
4. daily_matches (user_id + candidate_id 양쪽)
5. reports (reporter_id + reported_id 양쪽)
6. user_strikes 정리
7. user row 삭제
8. 카카오 unlink 호출 (best-effort)
```

### 정책
- ✅ CORS 허용 origin은 `FRONTEND_URLS` 콤마 분리로 명시
- ✅ JWT는 `Authorization: Bearer` 헤더로 전달
- ✅ 모든 사용자 식별 정보 (kakao_id 등)는 탈퇴 시 hard delete
- ✅ 탈퇴 시 카카오에 unlink 신호 전달 (PIPA 준수)
