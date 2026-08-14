# DB 최소권한 롤 분리 + RLS 재검토 (T-B03)

작성: 2026-08-13 · 대상: MeloBe 백엔드(FastAPI) ↔ Supabase PostgreSQL

이 문서는 **설계와 감사 결과**다. 운영 DB 에 대한 실행은 사람이 한다.
바로 실행할 SQL 은 `.melobe/state/NEEDS_HUMAN.md` 에 복사해 두었다.

---

## 1. 지금 무엇이 문제인가

FastAPI 는 `DATABASE_URL` 하나로 DB 에 붙고, 그 계정이 **테이블 소유자(`postgres`)** 다.
여기서 두 가지가 동시에 깨진다.

**(1) RLS 가 켜져도 무력하다.**
PostgreSQL 에서 **테이블 소유자는 자기 테이블의 RLS 정책을 우회한다.**
(`ALTER TABLE … FORCE ROW LEVEL SECURITY` 를 따로 켜지 않는 한.)
즉 지금 상태에서는 Supabase 콘솔에서 RLS 를 켜고 정책을 아무리 잘 써도
앱 커넥션에는 **아무 효과가 없다**. `backend/CLAUDE.md` 가 "superuser 라 RLS 무력화"라고
적은 것의 정확한 메커니즘이 이것이다 (Supabase 의 `postgres` 롤은 엄밀히는
superuser 가 아니지만, `public` 스키마 테이블의 **소유자**이므로 결과는 같다).

**(2) 앱이 DDL 을 할 수 있다.**
SQL 인젝션이나 실수 한 줄이 `DROP TABLE` 까지 갈 수 있다. 런타임 앱 코드는
DDL 이 전혀 필요 없다 — T-B01 이후 기동 시 스키마를 만들지 않고
`alembic_version` 을 **읽기만** 한다(`app/database.py:init_db`).

결과적으로 사용자 격리는 **전적으로 앱 레이어 필터**에 걸려 있다.
그 필터가 실제로 다 걸려 있는지가 §5 의 감사 대상이다.

---

## 2. 롤 설계

3개로 나눈다. 새로 만드는 것은 두 개다.

| 롤 | 누가 쓰나 | 할 수 있는 일 | 할 수 없는 일 |
|---|---|---|---|
| `melobe_app` | **FastAPI 런타임** (Render) | 지정된 테이블의 DML(§3) | DDL · 롤 생성 · 테이블 소유 · `audit_logs` 수정/삭제 |
| `melobe_migrator` | **Alembic 마이그레이션** (사람이 수동 실행) | 스키마 DDL, 데이터 백필 | 앱 트래픽을 받지 않음(상시 접속 아님) |
| `postgres` | **Supabase 콘솔 / 긴급 대응만** | 전부 | — (애플리케이션 커넥션 문자열에서 제거) |

원칙 세 가지.

- **`melobe_app` 은 어떤 테이블도 소유하지 않는다.** 소유하는 순간 §1 의 RLS 우회가 되살아난다.
- **`melobe_app` 은 `NOINHERIT` 로 만들지 않아도 되지만, 어떤 상위 롤에도 넣지 않는다.**
- **`public` 스키마의 기본 `CREATE` 권한을 회수한다.** PG15+ 는 기본으로 회수돼 있지만
  Supabase 는 프로젝트 생성 시점에 따라 다르므로 명시적으로 `REVOKE` 한다.

### 왜 `melobe_migrator` 가 테이블을 소유해야 하나

Alembic 이 `ALTER TABLE` 을 하려면 그 테이블의 소유자여야 한다. 지금은 `postgres` 가
소유자이므로, 마이그레이션도 `postgres` 로 돌 수밖에 없다. 소유권을 `melobe_migrator`
로 옮기면 `postgres` 는 콘솔 전용이 되고, DDL 은 사람이 명시적으로 migrator 자격으로
실행할 때만 가능해진다.

소유권을 옮겨도 Supabase 대시보드(Table Editor 등)가 계속 동작하도록
`GRANT melobe_migrator TO postgres;` 를 같이 준다(`postgres` 가 migrator 의 권한을 상속).

---

## 3. 권한 매트릭스 (`melobe_app`)

코드에서 실제로 실행되는 DML 만 준다. 근거 파일을 같이 적었다 — 나중에 코드가
바뀌어 권한이 모자라면 여기부터 본다.

| 테이블 | S | I | U | D | 근거 |
|---|:-:|:-:|:-:|:-:|---|
| `users` | ✅ | ✅ | ✅ | ✅ | 카카오 upsert(`services/auth.upsert_kakao_user`), 프로필 수정, 탈퇴 hard delete(`services/users.delete_account`) |
| `user_photos` | ✅ | ✅ | ✅ | ✅ | `services/photos.py` 전반 |
| `interview_answers` | ✅ | ✅ | — | ✅ | `routers/users.replace_interview_answers` 는 **삭제 후 재삽입**이라 UPDATE 가 없다 |
| `card_unlocks` | ✅ | ✅ | — | ✅ | `services/matching.py`, 탈퇴 정리 |
| `chat_threads` | ✅ | ✅ | ✅ | ✅ | `routers/chat.py` (읽음 표시·나가기·하드삭제) |
| `messages` | ✅ | ✅ | ✅ | ✅ | UPDATE 는 `services/audio_retention.purge_expired_audio` 가 `media_url` 을 NULL 로 만드는 경로뿐 |
| `user_blocks` | ✅ | ✅ | — | ✅ | 차단 생성, 탈퇴 정리 |
| `reports` | ✅ | ✅ | — | ✅ | `routers/reports.py`, 탈퇴 정리 |
| `user_strikes` | ✅ | ✅ | — | ✅ | `routers/chat._enforce_chat_moderation`, 탈퇴 정리 |
| `star_orders` | ✅ | ✅ | ✅ | ✅ | `services/payments.py` (PENDING→PAID), 탈퇴 정리 |
| `daily_ai_texts` | ✅ | ✅ | — | ✅ | `services/daily_ai.py`, 탈퇴 정리 |
| `device_tokens` | ✅ | ✅ | ✅ | ✅ | upsert(`routers/users.register_device_token`). **D 는 지금 코드엔 없지만 F-1 을 고치면 필요하다 — 미리 준다** |
| `knowledge_chunks` | ✅ | ❌ | ❌ | ❌ | **T-B08 에서 HTTP 적재 엔드포인트를 제거**해 앱에는 검색(SELECT) 경로만 남았다. 적재·번역·임베딩 재계산 스크립트는 migrator 로 돌린다 |
| `audit_logs` | ✅ | ✅ | ❌ | ❌ | **의도적으로 U/D 를 주지 않는다.** 감사 로그 append-only(ADM-LOG-001)를 앱 코드가 아니라 **DB 권한으로 강제**한다 |
| `alembic_version` | ✅ | — | — | — | 기동 시 리비전 확인(`init_db`)이 **읽는다**. 빠뜨리면 기동이 permission denied 로 죽는다 |

추가로 필요한 것:

- **시퀀스 USAGE.** 모든 PK 가 `SERIAL` 이라 INSERT 하려면 시퀀스 권한이 있어야 한다.
  `GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO melobe_app;`
- **`GRANT USAGE ON SCHEMA public`** (CREATE 는 주지 않는다).

---

## 4. 적용 절차

Phase 1 만 해도 §1 의 (2)가 해결되고, RLS 를 켤 수 있는 전제가 만들어진다.
**Phase 3(RLS 실제 적용)은 앱 코드 변경이 선행되어야 한다 — §6 참조. 지금 켜면 안 된다.**

### Phase 1 — 앱 롤 생성 + 커넥션 교체 (필수 · 되돌리기 쉬움)

1. 비밀번호를 생성한다(예: `openssl rand -base64 32`). **이 문서/저장소에 적지 않는다.**
2. Supabase SQL Editor 에서 SQL 실행 — 전문은 `.melobe/state/NEEDS_HUMAN.md` §0-B.
3. Render 환경변수 `DATABASE_URL` 을 `melobe_app` 자격으로 교체(§4.3).
4. 재배포 후 확인: `/health` 200, 로그인 → 오늘의 인연 → 채팅 전송이 되는지.
5. 실패 시 롤백: `DATABASE_URL` 을 원래 값으로 되돌리면 끝. 롤은 남아 있어도 무해하다.

**전제**: 이 시점에 `audit_logs` 와 `alembic_version` 이 이미 존재해야 한다
(T-B01/T-B02 스키마 정렬이 선행). 없는 테이블에 `GRANT` 하면 그 자리에서 에러가 난다.

### Phase 2 — 마이그레이터 롤 + 소유권 이전 (권장 · 나중에 해도 됨)

```sql
CREATE ROLE melobe_migrator WITH LOGIN PASSWORD '<MIGRATOR_PASSWORD>'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
GRANT USAGE, CREATE ON SCHEMA public TO melobe_migrator;

-- Supabase 대시보드·긴급 대응이 계속 되도록 postgres 가 migrator 를 상속하게 한다.
GRANT melobe_migrator TO postgres;

-- 소유권 이전. 이 순간부터 postgres 로 붙은 커넥션은 RLS 를 우회하지 않는다.
ALTER TABLE alembic_version    OWNER TO melobe_migrator;
ALTER TABLE audit_logs         OWNER TO melobe_migrator;
ALTER TABLE card_unlocks       OWNER TO melobe_migrator;
ALTER TABLE chat_threads       OWNER TO melobe_migrator;
ALTER TABLE daily_ai_texts     OWNER TO melobe_migrator;
ALTER TABLE device_tokens      OWNER TO melobe_migrator;
ALTER TABLE interview_answers  OWNER TO melobe_migrator;
ALTER TABLE knowledge_chunks   OWNER TO melobe_migrator;
ALTER TABLE messages           OWNER TO melobe_migrator;
ALTER TABLE reports            OWNER TO melobe_migrator;
ALTER TABLE star_orders        OWNER TO melobe_migrator;
ALTER TABLE user_blocks        OWNER TO melobe_migrator;
ALTER TABLE user_photos        OWNER TO melobe_migrator;
ALTER TABLE user_strikes       OWNER TO melobe_migrator;
ALTER TABLE users              OWNER TO melobe_migrator;

-- SERIAL 시퀀스도 같이 옮긴다.
DO $$
DECLARE s record;
BEGIN
  FOR s IN
    SELECT sequencename FROM pg_sequences WHERE schemaname = 'public'
  LOOP
    EXECUTE format('ALTER SEQUENCE public.%I OWNER TO melobe_migrator', s.sequencename);
  END LOOP;
END $$;
```

소유권이 바뀌어도 §3 에서 `melobe_app` 에 준 GRANT 는 그대로 유지된다.
이후 마이그레이션은 `DATABASE_URL=<migrator 자격> alembic upgrade head` 로 실행한다
(DDL 은 풀러 대신 Direct connection 을 쓰는 편이 안전하다).
롤백은 위 `ALTER TABLE … OWNER TO postgres;` 를 반대로 실행하면 된다.

> **Phase 2 이후 새 테이블을 만들 때 주의.** `GRANT … ON ALL TABLES` 는 실행 시점의
> 스냅샷이라, 이후 리비전이 만든 테이블에는 `melobe_app` 권한이 없다. 그대로 배포하면
> 런타임에 `permission denied` 가 난다. **테이블을 만드는 리비전 안에서 GRANT 도 같이
> 실행하는 것**을 규칙으로 한다 (`op.execute("GRANT SELECT, INSERT … TO melobe_app")`).
> `ALTER DEFAULT PRIVILEGES` 로 일괄 부여하는 방법도 있지만, 앞으로 생길 모든 테이블에
> 무조건 DML 을 주게 되어 이 문서의 목적과 어긋난다. 권하지 않는다.

### 4.3 교체할 `DATABASE_URL`

현재 값(추정):

```
postgresql+asyncpg://postgres.<PROJECT_REF>:<현재비밀번호>@aws-0-<REGION>.pooler.supabase.com:5432/postgres
```

바꿀 값:

```
postgresql+asyncpg://melobe_app.<PROJECT_REF>:<APP_PASSWORD>@aws-0-<REGION>.pooler.supabase.com:5432/postgres
```

- `<PROJECT_REF>`·`<REGION>` 은 **현재 값에서 그대로 가져온다.** 새로 만들지 않는다.
- Supabase 풀러(Supavisor)는 사용자명을 `<롤>.<project_ref>` 형식으로 받는다.
  포트 `5432` = Session pooler(현재 사용 중), `6543` = Transaction pooler.
  **포트를 바꾸지 말 것** — 앱은 Session 모드를 전제로 `statement_cache_size=0` 설정을 쓴다.
- ⚠️ **확인 필요**: Supavisor 가 커스텀 롤 로그인을 허용하는지는 프로젝트 설정에 따라
  다를 수 있다. 풀러로 접속이 안 되면 직접 접속
  (`db.<PROJECT_REF>.supabase.co:5432`, 사용자명은 `melobe_app`)으로 먼저 검증하고,
  풀러가 막혀 있으면 이 문서에 결과를 적어 주세요(T-B04 커넥션 풀 설계에 영향).

---

## 5. 코드 감사 — 사용자 격리 필터가 실제로 걸려 있는가

`app/routers/*` 와 `app/services/*` 의 모든 쿼리 지점을 테이블별로 확인했다.
기준은 "인증된 사용자 A 가 사용자 B 의 행을 읽거나 쓸 수 있는가".

### 5.1 격리가 걸려 있는 것 (문제 없음)

| 테이블 | 격리 방식 | 확인한 지점 |
|---|---|---|
| `chat_threads` | 항상 `_canonical_pair(current_user.id, peer_id)` 로 조회하거나 `user_a_id/user_b_id == current_user.id` 필터. `leave_thread` 는 `db.get` 후 **참여자 검증 후 403** | `routers/chat.py` 전체 |
| `messages` | 소속 스레드를 먼저 위와 같이 확정한 뒤 `thread_id` 로만 조회. 메시지 id 직접 조회 경로 없음 | `routers/chat.py:get_messages_with_peer` |
| `card_unlocks` | 모든 조회/집계가 `CardUnlock.user_id == user.id` | `services/matching.py` |
| `user_photos` | `delete_photo`/`set_primary` 가 `db.get` 후 `photo.user_id != user.id` 면 **404 로 반환** | `services/photos.py:63,95` |
| `interview_answers` | 내 것은 `user_id == current_user.id`. 남의 것은 §5.2 상호주의 규칙에 따라 제한 노출 | `routers/users.py`, `services/users.build_public_profile` |
| `star_orders` | `order_id` 로 찾은 뒤 `order.user_id != user.id` 면 404 | `services/payments.py:74` |
| `daily_ai_texts` | `user_id` + `kst_date` + `kind` 3중 필터 | `services/daily_ai.py:_find` |
| `user_strikes` | `user_id == user.id` | `routers/chat.py` |
| `audit_logs` | 앱 런타임에 읽는 경로 없음(T-E01 이 관리자 화면에서 쓴다) | `services/audit.py` |

### 5.2 의도된 전역 조회 (격리 대상 아님 — 확인만)

- `services/stats.py:_compute_global` — 가입자 수·성별 분포 등 **전역 집계**.
  개인 식별 정보를 반환하지 않고 7분 캐시. 정상.
- `services/compatibility.py:_candidate_pool` — 매칭 후보 풀. 자기 자신 제외 +
  양방향 차단 제외 + 성별 하드필터가 걸려 있다(OI-MATCH-001/OI-BLOCK-001 준수).
- `services/users.build_public_profile` — 남의 프로필을 **상호주의**로 가린다
  (내가 올린 사진 수·인터뷰 답변 수만큼만 상대 것이 보인다). 설계된 동작.
- `services/push.send_daily_match_push` — 전체 기기 토큰 순회. 배치 작업이라 정상.

### 5.3 발견된 결함

심각도 순. **이 태스크에서는 고치지 않고 기록만 한다**(범위 밖). 각 항목에 백로그 행을 만들었다.

#### F-1 (높음) — 탈퇴가 `device_tokens` 를 지우지 않아 **탈퇴 자체가 실패한다**

`services/users.delete_account` 는 12개 테이블을 정리하지만 `device_tokens` 를 빠뜨렸다.
`device_tokens.user_id` 는 `users.id` 를 참조하는 FK 이고 **`ON DELETE` 절이 없다**
(baseline 리비전 `1730ca475367:147` — 전 FK 에 `ondelete` 없음 = `NO ACTION`).

- 재현: 앱을 설치해 푸시 토큰이 등록된 계정이 탈퇴를 시도 → 마지막 `DELETE FROM users`
  에서 `ForeignKeyViolation` → 500. **실사용자 대부분이 여기 해당한다.**
- 테스트가 못 잡는 이유: 테스트 하네스가 SQLite 이고, SQLite 는 `PRAGMA foreign_keys`
  가 기본 OFF 다(`tests/conftest.py` 에서 켜지 않는다). Postgres 에서만 터진다.
- 부수 효과: 탈퇴가 실패하면 카카오 unlink(PIPA 대응)도 실행되지 않는다.
- → **T-B07**

#### F-2 (높음) — `/knowledge/*` 3개 엔드포인트에 인증이 없다

`routers/knowledge.py` 의 `POST /knowledge/chunks`, `POST /knowledge/ingest`,
`POST /knowledge/retrieve` 에 `Depends(get_current_user)` 가 없다. 인터넷의 누구나
RAG 원전 코퍼스에 텍스트를 **삽입**할 수 있다.

- 영향: 사주 풀이 LLM 의 grounding 문서에 임의 문장을 심을 수 있다(간접 프롬프트 인젝션).
  임베딩 생성 비용도 외부인이 태울 수 있다.
- 사용자 격리 문제는 아니지만(테이블이 사용자 소유가 아니다), 롤 설계에는 영향이 있다 —
  `melobe_app` 에 `knowledge_chunks` INSERT 를 주는 근거가 이 공개 엔드포인트뿐이다.
  인증을 붙이고 적재를 스크립트(migrator 롤)로 옮기면 INSERT 권한도 회수할 수 있다.
- → **T-B08 에서 해소.** 적재 2개(`/chunks`·`/ingest`)는 호출자가 없어 라우터에서 제거했고
  (`scripts/ingest_jsonl_to_db.py` 만 남는다), `/retrieve` 에는 `Depends(get_current_user)`
  를 붙였다. §3 매트릭스의 `knowledge_chunks` INSERT 도 회수했다.
  회귀 방어: `tests/test_knowledge_auth.py`

#### F-3 (중간) — 차단한 상대의 프로필·궁합을 계속 조회할 수 있다

`GET /users/{user_id}/public-profile` 과 `GET /compatibility/report/{peer_id}` 는
`is_blocked` 를 확인하지 않는다. `GET /recommendations/pair/{id}` 는 차단·열람을
모두 확인하는 것과 대조된다.

- 영향: 차단당한 사용자가 id 를 알면(또는 순차 대입으로) 상대의 닉네임·나이·지역·사진·
  일주(日柱) 등을 계속 볼 수 있다. DECISIONS OI-BLOCK-001 의 "양방향 제외"와 어긋난다.
- `public-profile` 은 카드 열람 여부와 무관하게 **모든 인증 사용자에게 열려** 있어
  id 순차 대입으로 회원 프로필을 열거할 수 있다.
- → **T-B09**

#### F-4 (낮음) — 기기 토큰 소유권이 검증 없이 이전된다

`routers/users.register_device_token` 은 같은 토큰이 이미 있으면 `user_id` 를
호출자로 덮어쓴다. 기기를 되판 경우를 위한 동작이지만, 남의 FCM 토큰 문자열을
아는 사람이 그 기기로 **자기 알림이 가게** 만들 수 있다. 토큰은 추측 불가능한
값이라 실현 가능성은 낮다. 기록만 해 둔다.

#### F-5 (참고) — `DEBUG=true` 면 인증이 완전히 우회된다

`core/deps.get_current_user` 는 `settings.debug` 가 참이면 `X-Dev-User-Id` 헤더만으로
임의 사용자가 된다. 이미 알려진 사항이고 `tests/test_isolation.py` 가 기본값을 고정하고
있지만, **운영 `DEBUG` 확인은 여전히 미완**이다(NEEDS_HUMAN 최상단).
롤 분리를 해도 이게 켜져 있으면 앱 레이어 격리는 통째로 무의미하다.

---

## 6. RLS 정책안 (Phase 3 — 아직 켜지 말 것)

### 6.1 선행 조건: 앱이 "현재 사용자"를 DB 에 알려야 한다

RLS 정책은 `current_setting('app.current_user_id')` 같은 세션 변수를 봐야 한다.
그런데 지금 구조에서 이걸 넣는 것은 단순하지 않다.

- 커넥션이 **풀링**된다(SQLAlchemy 풀 + Supabase 풀러). `SET` (세션 스코프)을 쓰면
  값이 다음 요청으로 **새어 나간다** → 다른 사용자의 데이터를 보게 된다. 최악의 실패다.
- 따라서 반드시 트랜잭션 스코프 `SET LOCAL` 이어야 한다.
- 그런데 앱은 **한 요청 안에서 여러 번 commit** 한다(예: `_enforce_chat_moderation` 은
  스트라이크 기록에서 한 번, 정지 처리에서 또 한 번). commit 이 트랜잭션을 끝내므로
  `SET LOCAL` 값이 사라진다. 요청 시작 시 한 번 거는 방식은 **작동하지 않는다.**
- 올바른 방법: SQLAlchemy `Session` 의 `after_begin` 이벤트에 훅을 걸어 **트랜잭션이
  새로 시작될 때마다** `SET LOCAL app.current_user_id = …` 를 다시 실행한다.
  `contextvars` 로 현재 요청의 사용자 id 를 전달한다.

이 작업은 T-B03 범위 밖이라 **T-B10** 으로 분리했다.

### 6.2 정책 초안

앱이 세션 변수를 넣을 수 있게 된 뒤에 적용한다.

```sql
-- 헬퍼: 세션 변수가 없으면 NULL (정책이 자동으로 거짓이 되어 아무것도 안 보인다)
CREATE OR REPLACE FUNCTION app_current_user_id() RETURNS integer
LANGUAGE sql STABLE AS $$
  SELECT NULLIF(current_setting('app.current_user_id', true), '')::integer
$$;

-- 예: 단순 소유 테이블 (card_unlocks, daily_ai_texts, interview_answers,
--     user_photos, user_strikes, star_orders, device_tokens)
ALTER TABLE card_unlocks ENABLE ROW LEVEL SECURITY;
CREATE POLICY card_unlocks_own ON card_unlocks
  FOR ALL TO melobe_app
  USING (user_id = app_current_user_id())
  WITH CHECK (user_id = app_current_user_id());
```

테이블별 술어(predicate)는 다음과 같다. **소유 관계가 단순하지 않은 것이 문제다.**

| 테이블 | USING 술어 | 비고 |
|---|---|---|
| `card_unlocks` | `user_id = app_current_user_id()` | `candidate_id` 쪽은 읽을 일이 없다 |
| `daily_ai_texts`, `interview_answers`, `user_photos`, `user_strikes`, `star_orders`, `device_tokens` | `user_id = app_current_user_id()` | 단순 소유 |
| `chat_threads` | `app_current_user_id() IN (user_a_id, user_b_id)` | |
| `messages` | `thread_id IN (SELECT id FROM chat_threads WHERE app_current_user_id() IN (user_a_id, user_b_id))` | 서브쿼리 비용 주의 — `messages(thread_id, id)` 인덱스는 이미 있다 |
| `user_blocks` | `app_current_user_id() IN (blocker_id, blocked_id)` | 양방향 조회를 하므로 둘 다 필요 |
| `reports` | `reporter_id = app_current_user_id()` | 신고당한 쪽은 자기 신고를 못 봐야 한다 |
| `audit_logs` | `false` (앱은 SELECT 불가) + INSERT 만 허용 | 관리자 앱은 별도 롤로 읽는다 |
| `users` | **단순 소유로 못 잠근다** | 아래 |
| `knowledge_chunks` | RLS 불필요 | 사용자 소유 아님 |

**`users` 가 RLS 의 실질적 한계다.** 매칭 후보 풀, 궁합 계산, 공개 프로필, 통계 집계가
전부 "남의 users 행"을 읽어야 성립한다. `id = app_current_user_id()` 로 잠그면 앱의
핵심 기능이 전부 죽는다. 현실적인 선택지는 셋이다.

1. `users` 에는 RLS 를 걸지 않고, 공개 가능한 컬럼만 담은 **뷰**(`public_profiles`)를 만들어
   앱이 후보 조회에 그 뷰만 쓰게 한다. `users` 직접 SELECT 권한은 자기 행으로 제한.
   → 가장 깔끔하지만 `services/compatibility`·`matching`·`stats` 의 쿼리를 다 바꿔야 한다.
2. `users` 는 SELECT 를 열어 두고(현행 유지), **쓰기만** `id = app_current_user_id()` 로 제한.
   → 비용이 가장 작다. "남의 프로필을 마음대로 고치는" 사고는 막고, 열람 통제는
   앱 레이어에 남긴다. **Phase 3 의 1차 목표로 이것을 권한다.**
3. 전면 RLS. 후보 조회를 `SECURITY DEFINER` 함수로 우회. → 우회 함수가 곧 새로운
   신뢰 경계가 되어 이득이 적다. 권하지 않는다.

### 6.3 RLS 를 켤 때 반드시 같이 할 것

- `ALTER TABLE … FORCE ROW LEVEL SECURITY` 는 **소유자(migrator)에게도** 정책을 적용한다.
  마이그레이션 백필이 막힐 수 있으므로 켜지 않는다. 대신 §2 원칙대로
  **`melobe_app` 이 소유자가 아니게** 유지하는 것으로 충분하다.
- 정책을 만든 뒤 `SET ROLE melobe_app;` 으로 대표 쿼리를 직접 돌려 검증한다.
  검증 없이 배포하면 "전부 0건 반환"이 조용히 나간다.

---

## 7. 사람이 판단해야 할 것

1. Supabase 풀러가 커스텀 롤 로그인을 허용하는가 (§4.3).
2. Phase 2(소유권 이전)를 지금 할지, Phase 1 만 하고 나중에 할지.
3. §6.2 의 `users` 처리 방식 1/2/3 중 무엇으로 갈지. (문서의 권고는 **2번**)
