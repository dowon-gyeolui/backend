ZAMI 백엔드 개요


1. 프로젝트

ZAMI 데이팅 앱의 FastAPI 백엔드. 사주/자미두수 계산, 매칭, 채팅,
사진 업로드, 모더레이션, 카카오 OAuth, 결제 (예정) 등을 담당.

배포: Render (api.thezami.io)
DB: PostgreSQL (프로덕션), SQLite (로컬 개발)


2. 기술 스택

FastAPI 0.115.0
Uvicorn 0.30.6
Pydantic 2.9.2 + pydantic-settings 2.5.2
SQLAlchemy 2.0.36 (async)
asyncpg (PostgreSQL), aiosqlite (SQLite)
httpx 0.27.2
openai (LLM 호출, omni-moderation)
boto3 (AWS Rekognition)
cloudinary (이미지 호스팅)
python-jose (JWT)
korean-lunar-calendar (음력 변환)
python-multipart (파일 업로드)


3. 폴더 구조

backend/app
    main.py                  앱 부팅, CORS, 라우터 등록, init_db
    config.py                Pydantic Settings (환경변수)
    database.py              SQLAlchemy 세션 + 부팅 시 dev 마이그레이션

    core
        deps.py              get_current_user (JWT 검증 dependency)

    models                   SQLAlchemy ORM 정의
        __init__.py          모든 모델 import (create_all 등록용)
        user.py              User
        chat.py              ChatThread, Message
        photo.py             UserPhoto (다중 사진 갤러리)
        daily_match.py       DailyMatch (4-슬롯 사이클)
        moderation.py        UserStrike (위반 기록)
        report.py            Report (사용자 신고)
        knowledge.py         KnowledgeChunk (RAG)

    schemas                  Pydantic 응답/요청 모델
        user.py              UserProfileResponse, ProfileUpdate, PublicProfileResponse 등
        chat.py              MessageOut, ChatThreadSummary 등
        compatibility.py     MatchCandidate, DailyMatchPack, DestinyAnalysis 등
        photo.py             UserPhotoResponse, UserPhotoListResponse
        saju.py              SajuResponse, Pillar, JamidusuResponse 등
        recommendation.py    PairRecommendation
        report.py            ReportCreate
        knowledge.py         KnowledgeChunkOut

    routers                  HTTP 라우트
        auth.py              /auth/kakao, /auth/kakao/callback
        users.py             /users/me, /users/me/photo, /users/me/photos, /users/{id}/public-profile
        saju.py              /saju/me, /saju/me/detailed, /saju/me/jamidusu
        compatibility.py     /compatibility/score, /matches, /today, /history,
                             /report, /destiny, /date-recommendation
        chat.py              /chat/threads, /chat/with/{peer}/messages, /read,
                             /threads/{id} (DELETE), /unread-summary
        recommendations.py   /recommendations/me, /pair/{id}
        reports.py           /reports
        knowledge.py         /knowledge/chunks, /ingest, /retrieve

    services
        auth.py              카카오 OAuth (token 교환, 프로필, unlink)
        users.py             User CRUD + delete_account FK 정리 + public_profile
        photos.py            다중 사진 갤러리 (add/delete/set_primary)
        photo_moderation.py  AWS Rekognition (얼굴/NSFW 검증)
        chat_moderation.py   채팅 3-레이어 (정규식, 욕설사전, OpenAI Moderation)
        compatibility.py     사주 호환성 점수 + 매칭 + 4-슬롯 daily 사이클 + history
        saju.py              사주 계산 메인
        saju_chart.py        명식 룩업 (천간/지지/십성/지장간/12운성/12신살)
        saju_engine.py       절기 + 일간/월간/년간/시간 계산 엔진
        recommendations.py   페어 추천 LLM
        storage.py           Cloudinary 업로드/삭제 (사진, 채팅 미디어)
        knowledge            RAG (chunking, embedding, retrieval, ingestion)
        llm
            interpret.py     OpenAI 기반 사주/페어/자미두수/데이트 LLM 호출

    data
        raw                  원전 텍스트 (궁통보감, 자미두수전서, 적천수천미)
        processed            JSONL 변환 + 한글 번역본

    scripts
        build_knowledge_jsonl.py  원전 -> JSONL 변환
        ingest_jsonl_to_db.py     JSONL -> DB embedding
        translate_chunks.py       chunk 한글 번역
        seed_demo_users.py        데모 사용자 생성
        unblind_all_users.py      모든 사용자 is_paid=True 토글 (테스트용)
        validate_jsonl.py         JSONL 스키마 검증


4. 데이터 모델

users
    id, kakao_id (unique), nickname, photo_url
    birth_date, birth_time, calendar_type, is_leap_month, gender, birth_place
    bio, height_cm, mbti, job, region, smoking, drinking, religion
    is_paid (프리미엄 여부)
    chat_suspended_until (모더레이션 정지 만료 시각)
    created_at, updated_at

user_photos
    id, user_id (FK), url, public_id (Cloudinary)
    position (정렬 순서), is_primary (메인 사진 여부)
    created_at

chat_threads
    id, user_a_id, user_b_id (canonical small/large)
    user_a_last_read_id, user_b_last_read_id (unread 계산용)
    user_a_left, user_b_left (소프트 leave 플래그)
    created_at, updated_at (마지막 메시지 시각으로 자동 갱신)

messages
    id, thread_id, sender_id
    content (text), media_url, media_type (image/audio)
    created_at

daily_matches
    id, user_id, candidate_id, slot_index (0-3), assigned_at
    한 묶음(=cycle)은 같은 assigned_at 공유
    KST 정오 12:00 anchor

user_strikes
    id, user_id, kind (contact_leak/profanity/harassment/sexual/spam/other), detail
    created_at
    24시간 내 누적 3회 시 chat_suspended_until 세팅

reports
    id, reporter_id, reported_id, reason, details
    created_at

knowledge_chunks
    id, source_title, source_author, topic, chapter
    content_original (한문), content_korean (번역)
    embedding (vector, RAG 검색용)
    citation, content_hash


5. 주요 API 엔드포인트

인증
    GET  /auth/kakao                    카카오 동의 페이지로 redirect
    GET  /auth/kakao/callback           code -> JWT 발급 -> 프론트 redirect

사용자
    GET    /users/me                    내 프로필
    PATCH  /users/me/profile            닉네임/사진 외 (bio, mbti, job 등)
    POST   /users/me/birth-data         생년월일 등 필수 정보 저장
    PATCH  /users/me/birth-data         부분 수정
    POST   /users/me/photo              레거시 단일 사진 업로드
    GET    /users/me/photos             내 갤러리 사진 리스트
    POST   /users/me/photos             갤러리에 사진 추가 (최대 6장)
    DELETE /users/me/photos/{id}        사진 삭제 (Cloudinary 도 destroy)
    PATCH  /users/me/photos/{id}/primary  메인 사진으로 지정
    POST   /users/me/upgrade-demo       is_paid 플래그 데모 토글
    DELETE /users/me                    탈퇴 (FK 정리 + 카카오 unlink)
    GET    /users/{id}/public-profile   상대 공개 프로필 (paywall 적용)

사주
    GET /saju/me                        4기둥 + 오행 + 짧은 해석
    GET /saju/me/detailed               + 4섹션 LLM 풀이 (성격/연애/재물/조언)
    GET /saju/me/jamidusu               자미두수 12궁 + 14주성 (프리미엄)

매칭
    GET /compatibility/score/{user_id}        두 사용자 간 호환성 점수
    GET /compatibility/matches?top_k=N        탑-N 매칭 후보 (레거시)
    GET /compatibility/today                  오늘의 4-슬롯 카드 팩
    GET /compatibility/history                누적 매칭 히스토리 (dedup)
    GET /compatibility/report/{peer_id}       채팅 헤더 운명 분석 리포트
    GET /compatibility/destiny/{peer_id}      운명의 실타래 5섹션 LLM 풀이
    GET /compatibility/date-recommendation/{peer_id}  데이트 장소 4-5개 추천

채팅
    GET    /chat/threads                          내 thread 리스트 (unread 포함)
    GET    /chat/threads/{id} (없음, DELETE만)
    DELETE /chat/threads/{id}                     소프트 leave
    GET    /chat/unread-summary                   nav 빨간 점용 합계
    POST   /chat/with/{peer}/read                 mark read
    GET    /chat/with/{peer}/messages?after_id=N  메시지 폴링
    POST   /chat/with/{peer}/messages             텍스트 메시지 전송
    POST   /chat/with/{peer}/messages/media       이미지/음성 메시지

추천 / 신고 / 지식
    GET  /recommendations/me            개인 추천
    GET  /recommendations/pair/{id}     페어 추천
    POST /reports                       사용자 신고
    GET  /knowledge/chunks              원전 청크 조회
    POST /knowledge/ingest              JSONL 인제스트
    GET  /knowledge/retrieve?q=...      RAG 검색


6. 핵심 기능

가) 카카오 OAuth
    services/auth.py — 토큰 교환, 프로필 조회, unlink
    탈퇴 시 unlink_kakao_user(kakao_id) 자동 호출
    KAKAO_ADMIN_KEY 환경변수로 인증 (사용자 access_token 불필요)

나) 사주 계산
    services/saju_engine.py — 절기 기준 일/월/년/시간 계산
    services/saju_chart.py — 천간/지지/십성/지장간/12운성/12신살 룩업
    services/saju.py — 메인 진입점, 캐시 친화적
    음력 변환 지원 (korean-lunar-calendar)
    출생지 기반 지역시 보정 (services/saju.py)

다) RAG (Retrieval-Augmented Generation)
    원전: 궁통보감 (여춘태), 자미두수전서, 적천수천미
    JSONL chunk + 한글 번역
    embedding 으로 유사도 검색
    LLM 풀이 시 관련 원전 구절 grounding

라) LLM 사용
    OpenAI gpt-4o-mini (cost-optimized)
    services/llm/interpret.py 에서 5가지 풀이 함수
        generate_saju_interpretation       단순 사주 풀이
        generate_detailed_interpretation   4섹션 (성격/연애/재물/조언)
        generate_jamidusu_interpretation   자미두수 12궁
        generate_destiny_analysis          페어 5섹션 (운명의 실타래)
        generate_date_recommendation       데이트 장소
        generate_pair_recommendation       페어 추천
    omni-moderation-latest (채팅 모더레이션, 무료)

마) 4-슬롯 매칭 (compatibility.py)
    KST 정오 12:00 anchor (모든 사용자가 같은 사이클 시각 공유)
    한 cycle = 96시간 (4일)
        slot 0,1: 즉시 unlock
        slot 2,3: assigned_at + 72시간 후 unlock
    GET /today: 현재 사이클 4장 반환, 96h 지나면 새 사이클 자동 생성
    GET /history: 누적 노출된 모든 후보 (candidate_id 기준 dedup)

바) 다중 사진 갤러리
    services/photos.py — 사용자별 최대 6장
    is_primary 플래그로 메인 사진 1장 선택
    삭제 시 Cloudinary asset 도 destroy
    삭제 시 다른 사진을 자동으로 메인으로 승격

사) 사진 모더레이션 (AWS Rekognition)
    services/photo_moderation.py
    DetectFaces: 얼굴 0개 / 2개 이상 / 면적 8% 미만 / 신뢰도 90% 미만 거절
    DetectModerationLabels: NSFW/폭력 카테고리 거절
    Cloudinary 업로드 BEFORE 호출 (실패 시 비용 절감)
    AWS 자격증명 미설정 시 graceful pass-through

아) 채팅 모더레이션 (3-레이어)
    services/chat_moderation.py
    Layer 1 정규식 (0.5ms): 휴대폰/카톡ID/SNS 핸들/URL leak 차단
    Layer 2 욕설 사전 (0.1ms): 한국어 + 영어 욕설 30+ 단어 + 변형
    Layer 3 OpenAI Moderation (200ms): 괴롭힘/위협/성적 컨텍스트
    위반 시 user_strikes 기록
    24h 내 누적 3회 -> chat_suspended_until 24h

자) Cloudinary 사진 처리 (storage.py)
    EXIF 회전 적용 (angle: exif) -> 안드로이드 사진 깨짐 방지
    HEIC -> JPG 변환 (format: jpg) -> iOS 호환
    800x800 limit + quality auto -> 사이즈 절감
    public_id 으로 동일 사용자 사진 덮어쓰기

차) 자동 마이그레이션 (database.py)
    init_db() — Base.metadata.create_all (신규 테이블 자동 생성)
    _dev_migrate_sqlite / _dev_migrate_postgres
        ALTER TABLE ADD COLUMN IF NOT EXISTS 로 누락된 컬럼 자동 추가
    Alembic 도입 전까지의 임시 방편


7. 외부 통합

Cloudinary
    프로필 사진 + 갤러리 + 채팅 미디어 호스팅
    EXIF 자동 회전 + JPG 변환
    delete_image 으로 삭제 시 Cloudinary asset 정리

OpenAI
    gpt-4o-mini: 사주 LLM 풀이
    omni-moderation-latest: 채팅 모더레이션 (한국어 지원, 무료)
    text-embedding-3-small: RAG 검색용 (knowledge ingestion)

AWS Rekognition
    DetectFaces (얼굴 검증)
    DetectModerationLabels (NSFW)
    region: ap-northeast-2 (서울)

Kakao
    OAuth 2.0 로그인
    /v1/user/unlink (탈퇴 시 동의 해제)


8. 환경변수

DATABASE_URL                PostgreSQL (postgresql+asyncpg://...) 또는 기본 SQLite

KAKAO_CLIENT_ID             카카오 REST API 키
KAKAO_CLIENT_SECRET         카카오 client secret (옵션)
KAKAO_REDIRECT_URI          OAuth 콜백 URI (https://api.thezami.io/auth/kakao/callback)
KAKAO_ADMIN_KEY             탈퇴 시 unlink 호출용 어드민 키

SECRET_KEY                  JWT 서명용 시크릿
ACCESS_TOKEN_EXPIRE_MINUTES JWT 만료 (기본 7일)

FRONTEND_URLS               콤마 구분 (CORS 허용 + OAuth redirect 첫 항목)
                            예: https://thezami.io,https://www.thezami.io,https://jamidusu-gamma.vercel.app,http://localhost:3000

OPENAI_API_KEY              OpenAI API 키
OPENAI_INTERPRET_MODEL      LLM 모델명 (기본 gpt-4o-mini)

CLOUDINARY_URL              cloudinary://api_key:api_secret@cloud_name 형식 (또는 아래 3개 분리)
CLOUDINARY_CLOUD_NAME
CLOUDINARY_API_KEY
CLOUDINARY_API_SECRET

AWS_ACCESS_KEY_ID           IAM 사용자 액세스 키 (AmazonRekognitionReadOnlyAccess)
AWS_SECRET_ACCESS_KEY       IAM 사용자 비밀 키
AWS_REGION                  ap-northeast-2

DEBUG                       SQLAlchemy echo 등 디버그 로그


9. 빌드 / 배포

GitHub: dowon-gyeolui/backend (main 브랜치)
Render: 자동 배포 (main 푸시 시 트리거)
도메인: api.thezami.io (가비아 DNS CNAME -> Render)

부팅 시 자동 작업:
    - 모든 모델 import 등록 (app/models/__init__.py)
    - Base.metadata.create_all (신규 테이블 생성)
    - _dev_migrate (누락된 컬럼 자동 추가)


10. 개발 워크플로우

로컬 개발:
    cd backend
    pip install -r requirements.txt
    cp .env.example .env  (환경변수 채우기)
    uvicorn app.main:app --reload --port 8000

데이터 인제스트:
    python scripts/build_knowledge_jsonl.py    원전 -> JSONL
    python scripts/translate_chunks.py         한글 번역
    python scripts/ingest_jsonl_to_db.py       embedding + DB 저장
    python scripts/seed_demo_users.py          데모 사용자 생성


11. 보안 / 컴플라이언스 메모

탈퇴 시 처리 순서 (services/users.delete_account):
    1) 채팅 thread + 메시지 정리
    2) 본인 메시지 (sender_id) 정리
    3) 사진 + Cloudinary asset 정리
    4) daily_matches (user_id + candidate_id 양쪽)
    5) reports (reporter_id + reported_id 양쪽)
    6) user_strikes 정리
    7) user row 삭제
    8) 카카오 unlink 호출 (best-effort)

CORS 허용 origin 은 FRONTEND_URLS 에서 콤마 분리로 명시
JWT 는 Authorization: Bearer 헤더로 전달
모든 사용자 식별 정보 (kakao_id 등) 는 탈퇴 시 hard delete
