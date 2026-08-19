"""관리자 매칭 대상자·후보 검증 (ADM-MATCH-001/002).

이 모듈은 추천을 만들지 않는다. **추천 엔진이 이미 하는 판정을 되짚어 근거로 펼친다.**
그래서 조건을 여기서 새로 정의하지 않고, 엔진이 쓰는 것을 그대로 가져다 쓴다.

- 하드필터: `services/compatibility.candidate_intrinsic_condition()` 과
  `allowed_candidate_genders()` — 후보군 SQL 이 쓰는 바로 그 조건.
- 선호(소프트) 조건과 완화 단계: `services/matching._matches` · `_relaxation_configs`.

그럼에도 **판정을 파이썬으로 한 벌 더 쓰는 곳이 하나 있다**(`hard_failures`). 후보마다
"어느 조건에서 탈락했는가"를 말하려면 조건을 하나씩 따로 물어봐야 하고, SQL 의 WHERE 는
통째로 참/거짓만 답하기 때문이다. 두 벌이 된 이상 어긋날 수 있으므로, 검증 응답은
**엔진의 실제 후보군과 이 판정을 맞대 본 숫자 두 개**(`hard_filter_violations`,
`unexpected_exclusions`)를 항상 함께 싣는다. 어긋나면 화면에 즉시 드러난다.

성능: 대상자 목록은 회원 수 × 회원 수로 조건을 판정한다. 출시 전 규모(수백 명)에서는
한순간이고, 관리 화면 하나를 위해 집계 전용 질의를 따로 두면 위에서 말한 "조건이 두 벌"
문제가 세 벌이 된다. 회원이 수천 명을 넘기면 그때 집계 질의로 바꾼다.
"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.block import UserBlock
from app.models.card_unlock import CardUnlock
from app.models.user import STATUS_ACTIVE, User
from app.schemas.admin_matching import (
    CandidateRow,
    ConditionSnapshot,
    ExcludedRow,
    FilterCode,
    SoftCheck,
    StageResult,
    TargetListItem,
    TargetVerification,
)
from app.services import admin_members
from app.services.compatibility import (
    _candidate_pool,
    _compute_age,
    allowed_candidate_genders,
)
from app.services.matching import _matches, _relaxation_configs

# 후보·제외 목록에 실어 내리는 최대 행 수. 전체 건수는 따로 내려가므로 숫자는 정확하고,
# 목록만 잘린다. 회원이 늘어도 응답 하나가 수 MB 가 되지 않게 하려는 상한이다.
_ROW_LIMIT = 100

# --- 하드필터 (OI-MATCH-001) ---------------------------------------------------
# **선언 순서가 곧 표시 순서이자 "최초 탈락 조건"의 기준**이다. OI-MATCH-001 이 나열한
# 순서(성별·차단·계정 정지)를 먼저 두고, 프로필 미완성은 뒤에 둔다. 운영자가 알고 싶은
# 것은 "제재 때문인가, 프로필이 덜 찼기 때문인가"이고 앞의 것이 먼저 눈에 들어와야 한다.
#
# 탈퇴는 코드가 없다. 탈퇴는 행을 지우는 hard delete 라(PIPA) 탈퇴 회원은 후보 목록에
# 나타날 수 없다 — 판정할 대상 자체가 존재하지 않는다.
HARD_SELF = "SELF"
HARD_GENDER = "GENDER"
HARD_BLOCK = "BLOCK"
HARD_STATUS = "STATUS"
HARD_PROFILE_HIDDEN = "PROFILE_HIDDEN"
HARD_NO_BIRTH_DATE = "NO_BIRTH_DATE"
HARD_NO_PHOTO = "NO_PHOTO"

HARD_FILTERS: tuple[FilterCode, ...] = (
    FilterCode(code=HARD_SELF, label="본인", description="자기 자신은 후보가 되지 않아요."),
    FilterCode(
        code=HARD_GENDER,
        label="성별",
        description="대상자의 성별에 맞는 이성만 후보가 돼요.",
    ),
    FilterCode(
        code=HARD_BLOCK,
        label="차단",
        description="어느 쪽이 차단했든 양방향으로 제외돼요 (OI-BLOCK-001).",
    ),
    FilterCode(
        code=HARD_STATUS,
        label="계정 정지",
        description="비활성·차단 상태의 회원은 후보가 되지 않아요.",
    ),
    FilterCode(
        code=HARD_PROFILE_HIDDEN,
        label="프로필 노출 중단",
        description="운영이 프로필을 내린 회원은 후보에서 빠져요.",
    ),
    FilterCode(
        code=HARD_NO_BIRTH_DATE,
        label="생년월일 미입력",
        description="사주를 계산할 수 없어 궁합 점수가 나오지 않아요.",
    ),
    FilterCode(
        code=HARD_NO_PHOTO,
        label="대표 사진 없음",
        description="카드에 보여줄 사진이 없어요.",
    ),
)

# --- 선호(소프트) 조건 ----------------------------------------------------------
# 후보를 제외하지 않는다. 후보가 없으면 단계적으로 완화되므로 "탈락"이 아니라
# "몇 단계에서 들어오는가"의 문제다.
SOFT_AGE = "AGE"
SOFT_REGION = "REGION"
SOFT_HEIGHT = "HEIGHT"

SOFT_FILTERS: tuple[FilterCode, ...] = (
    FilterCode(code=SOFT_AGE, label="희망 나이", description="희망 나이 범위 안에 드는가."),
    FilterCode(code=SOFT_REGION, label="희망 지역", description="희망 지역과 같은가."),
    FilterCode(code=SOFT_HEIGHT, label="희망 키", description="희망 최소 키 이상인가."),
)

_STATE_RANK = {"error": 0, "no_candidate": 1, "ok": 2}


def hard_failures(
    target: User, candidate: User, blocked_ids: frozenset[int]
) -> list[str]:
    """이 후보가 어긴 하드필터 코드 목록. 빈 목록이면 후보군에 들어간다.

    `compatibility._candidate_pool` 의 WHERE 절과 같은 판정을 조건별로 쪼갠 것이다.
    첫 번째 원소가 "최초 탈락 조건"이 된다.
    """
    failures: list[str] = []
    if candidate.id == target.id:
        failures.append(HARD_SELF)
    genders = allowed_candidate_genders(target.gender)
    if genders is not None and candidate.gender not in genders:
        failures.append(HARD_GENDER)
    if candidate.id in blocked_ids:
        failures.append(HARD_BLOCK)
    if candidate.status != STATUS_ACTIVE:
        failures.append(HARD_STATUS)
    if candidate.profile_hidden:
        failures.append(HARD_PROFILE_HIDDEN)
    if candidate.birth_date is None:
        failures.append(HARD_NO_BIRTH_DATE)
    if candidate.photo_url is None:
        failures.append(HARD_NO_PHOTO)
    return failures


def target_issues(target: User) -> list[str]:
    """대상자 **자신** 때문에 추천이 성립하지 않는 사유.

    후보 쪽 하드필터와 구분해야 한다. 후보가 0명인 것과 대상자가 카드를 받을 수 없는
    상태인 것은 운영자가 할 일이 다르다 — 앞은 회원 모집, 뒤는 이 회원의 프로필 보완이다.
    """
    issues: list[str] = []
    if target.birth_date is None:
        issues.append("생년월일 미입력 — 궁합 점수를 계산할 수 없어 카드가 만들어지지 않아요")
    if not settings.allow_same_gender_match and allowed_candidate_genders(target.gender) is None:
        issues.append("성별 미입력 — 이성 조건이 적용되지 않아 모든 성별이 후보가 돼요")
    if target.status != STATUS_ACTIVE:
        issues.append("계정 상태가 정상이 아니에요 — 다른 회원의 후보에서도 제외돼요")
    return issues


def _soft_checks(target: User, candidate: User) -> list[SoftCheck]:
    """선호 조건별 통과 근거. 값을 함께 실어 화면에서 판단을 재현할 수 있게 한다.

    나이 조건은 최소·최대가 **둘 다** 있어야 적용된다 — `matching._matches` 가 그렇다.
    """
    age = _compute_age(candidate.birth_date)
    if target.pref_age_min is not None and target.pref_age_max is not None:
        age_passed = age is not None and target.pref_age_min <= age <= target.pref_age_max
        age_detail = (
            f"{age}세 (희망 {target.pref_age_min}~{target.pref_age_max}세)"
            if age is not None
            else f"나이 미상 (희망 {target.pref_age_min}~{target.pref_age_max}세)"
        )
    else:
        age_passed, age_detail = True, "희망 나이 미설정"

    if target.pref_region is not None:
        region_passed = candidate.region == target.pref_region
        region_detail = f"{candidate.region or '미입력'} (희망 {target.pref_region})"
    else:
        region_passed, region_detail = True, "희망 지역 미설정"

    if target.pref_height_min is not None:
        height_passed = (
            candidate.height_cm is not None
            and candidate.height_cm >= target.pref_height_min
        )
        height_detail = (
            f"{candidate.height_cm}cm" if candidate.height_cm is not None else "키 미입력"
        ) + f" (희망 {target.pref_height_min}cm 이상)"
    else:
        height_passed, height_detail = True, "희망 키 미설정"

    return [
        SoftCheck(code=SOFT_AGE, label="희망 나이", passed=age_passed, detail=age_detail),
        SoftCheck(
            code=SOFT_REGION, label="희망 지역", passed=region_passed, detail=region_detail
        ),
        SoftCheck(
            code=SOFT_HEIGHT, label="희망 키", passed=height_passed, detail=height_detail
        ),
    ]


def _stage_label(config: tuple[int | None, int | None, str | None, int | None]) -> str:
    age_min, age_max, region, height_min = config
    return " · ".join(
        [
            f"나이 {age_min}~{age_max}세"
            if age_min is not None and age_max is not None
            else "나이 제한 없음",
            f"지역 {region}" if region else "지역 제한 없음",
            f"키 {height_min}cm 이상" if height_min is not None else "키 제한 없음",
        ]
    )


def _block_partners(rows) -> dict[int, set[int]]:
    """차단은 양방향이다(OI-BLOCK-001). 방향을 지우고 상대 id 만 모은다."""
    partners: dict[int, set[int]] = {}
    for blocker_id, blocked_id in rows:
        partners.setdefault(blocker_id, set()).add(blocked_id)
        partners.setdefault(blocked_id, set()).add(blocker_id)
    return partners


def _list_item(
    target: User, users: list[User], blocked_ids: frozenset[int]
) -> TargetListItem:
    pool = [u for u in users if not hard_failures(target, u, blocked_ids)]
    preferred = [
        c
        for c in pool
        if _matches(
            c,
            age_min=target.pref_age_min,
            age_max=target.pref_age_max,
            region=target.pref_region,
            height_min=target.pref_height_min,
        )
    ]
    issues = target_issues(target)
    return TargetListItem(
        id=target.id,
        nickname=target.nickname,
        gender=target.gender,
        status=target.status,
        profile_hidden=target.profile_hidden,
        created_at=target.created_at,
        state="error" if issues else ("no_candidate" if not pool else "ok"),
        issues=issues,
        pool_count=len(pool),
        preferred_count=len(preferred),
    )


async def _all_users(db: AsyncSession) -> list[User]:
    return list((await db.execute(select(User))).scalars())


async def _blocked_ids(db: AsyncSession, user_id: int) -> frozenset[int]:
    rows = (
        await db.execute(
            select(UserBlock.blocker_id, UserBlock.blocked_id).where(
                (UserBlock.blocker_id == user_id) | (UserBlock.blocked_id == user_id)
            )
        )
    ).all()
    return frozenset(_block_partners(rows).get(user_id, set()))


async def list_targets(
    db: AsyncSession,
    *,
    q: str | None,
    state: str | None,
    page: int,
    size: int,
) -> tuple[int, list[TargetListItem]]:
    """대상자 목록. **문제가 있는 대상자가 먼저 온다** (ADM-MATCH-001).

    검색어 해석은 회원 목록(T-E03)의 것을 그대로 쓴다. 관리자가 회원 화면에서 찾은
    회원을 여기서도 같은 방법으로 찾을 수 있어야 한다.
    """
    targets = list(
        (
            await db.execute(
                admin_members.build_list_query(q=q, status=None, matchable=None)
            )
        ).scalars()
    )
    users = await _all_users(db)
    partners = _block_partners(
        (await db.execute(select(UserBlock.blocker_id, UserBlock.blocked_id))).all()
    )

    items = [
        _list_item(t, users, frozenset(partners.get(t.id, set()))) for t in targets
    ]
    if state:
        items = [i for i in items if i.state == state]
    # 질의가 이미 가입일 내림차순으로 준 순서를 유지한 채(파이썬 정렬은 안정적이다)
    # 상태와 후보 수로만 다시 세운다.
    items.sort(key=lambda i: (_STATE_RANK[i.state], i.pool_count))
    return len(items), items[(page - 1) * size : page * size]


async def verify(db: AsyncSession, target: User) -> TargetVerification:
    """후보 검증 — 조건 스냅샷 · 후보별 통과 근거 · 제외 후보와 최초 탈락 조건.

    마지막에 엔진의 실제 후보군(`_candidate_pool`)을 한 번 더 불러 이 판정과 맞대 본다.
    같은 질문을 두 경로로 물어 답이 다르면 화면에 숫자로 드러나게 하려는 것이다.
    """
    users = await _all_users(db)
    blocked_ids = await _blocked_ids(db, target.id)
    unlocked = set(
        (
            await db.execute(
                select(CardUnlock.candidate_id).where(CardUnlock.user_id == target.id)
            )
        )
        .scalars()
        .all()
    )

    passed: list[User] = []
    excluded: list[ExcludedRow] = []
    for user in users:
        failures = hard_failures(target, user, blocked_ids)
        if not failures:
            passed.append(user)
        elif user.id != target.id:
            # 대상자 본인은 제외 목록에 싣지 않는다. 모든 대상자에게 항상 한 줄씩 붙어
            # 실제로 봐야 할 제외 사유를 밀어낼 뿐이다. 조건 자체는 범례에 남아 있다.
            excluded.append(
                ExcludedRow(
                    id=user.id,
                    nickname=user.nickname,
                    gender=user.gender,
                    first_code=failures[0],
                    codes=failures,
                )
            )

    configs = _relaxation_configs(target)
    stage_of: dict[int, int] = {}
    stages: list[StageResult] = []
    applied_index: int | None = None
    for index, config in enumerate(configs):
        age_min, age_max, region, height_min = config
        matched = [
            c
            for c in passed
            if _matches(
                c, age_min=age_min, age_max=age_max, region=region, height_min=height_min
            )
        ]
        for c in matched:
            stage_of.setdefault(c.id, index)
        if applied_index is None and matched:
            applied_index = index
        stages.append(
            StageResult(
                index=index,
                label=_stage_label(config),
                match_count=len(matched),
                applied=False,
            )
        )
    if applied_index is not None:
        stages[applied_index].applied = True

    # 엔진의 실제 후보군과 맞대 본다. 둘 다 0이어야 한다.
    engine_ids = {c.id for c in await _candidate_pool(target, db)}
    passed_ids = {c.id for c in passed}

    candidates = [
        CandidateRow(
            id=c.id,
            nickname=c.nickname,
            gender=c.gender,
            age=_compute_age(c.birth_date),
            region=c.region,
            height_cm=c.height_cm,
            already_unlocked=c.id in unlocked,
            soft_checks=_soft_checks(target, c),
            stage_index=stage_of.get(c.id),
        )
        for c in passed
    ]
    # 완화 단계가 이른 후보가 먼저 온다. 단계가 같으면 아직 열람하지 않은 쪽이 먼저다.
    candidates.sort(
        key=lambda c: (
            c.stage_index if c.stage_index is not None else len(configs),
            c.already_unlocked,
            c.id,
        )
    )

    genders = allowed_candidate_genders(target.gender)
    unlocked_in_pool = len(passed_ids & unlocked)
    return TargetVerification(
        target=_list_item(target, users, blocked_ids),
        snapshot=ConditionSnapshot(
            evaluated_at=datetime.now(timezone.utc),
            allow_same_gender=settings.allow_same_gender_match,
            candidate_genders=None if genders is None else list(genders),
            pref_age_min=target.pref_age_min,
            pref_age_max=target.pref_age_max,
            pref_region=target.pref_region,
            pref_height_min=target.pref_height_min,
        ),
        hard_filters=list(HARD_FILTERS),
        soft_filters=list(SOFT_FILTERS),
        total_members=len(users),
        pool_count=len(passed),
        unlocked_count=unlocked_in_pool,
        available_count=len(passed) - unlocked_in_pool,
        hard_filter_violations=len(engine_ids - passed_ids),
        unexpected_exclusions=len(passed_ids - engine_ids),
        stages=stages,
        applied_stage_index=applied_index,
        candidates=candidates[:_ROW_LIMIT],
        candidates_total=len(candidates),
        excluded=excluded[:_ROW_LIMIT],
        excluded_total=len(excluded),
    )
