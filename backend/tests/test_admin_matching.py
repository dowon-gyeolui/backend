"""관리자 매칭 대상자·후보 검증 (T-E04) — QA 체크포인트를 고정한다.

ADM-MATCH-002 가 요구하는 것은 두 가지다.

1. **하드필터 위반 후보가 결과에 0건** — 화면에서 확인할 수 있어야 한다.
2. **제외 사유가 조건 코드로 표시** — "그냥 안 나왔다"가 아니라 어느 조건에서
   처음 떨어졌는지가 보여야 한다.

여기서 가장 중요한 테스트는 `test_verification_agrees_with_engine_pool` 이다.
이 화면은 조건을 파이썬으로 한 벌 더 판정하므로(조건별 사유를 말하려면 그래야 한다),
그 판정이 엔진의 후보군 SQL 과 어긋나면 화면이 거짓말을 하게 된다. 두 경로를 맞대는
테스트가 없으면 그 거짓말은 아무도 모르는 채로 남는다.
"""

from datetime import date

from app.core.admin_rbac import ROLE_VIEWER
from app.models.block import UserBlock
from app.models.user import STATUS_BLOCKED, STATUS_INACTIVE
from app.services.admin_matching import (
    HARD_BLOCK,
    HARD_GENDER,
    HARD_NO_BIRTH_DATE,
    HARD_NO_PHOTO,
    HARD_PROFILE_HIDDEN,
    HARD_SELF,
    HARD_STATUS,
    hard_failures,
)
from app.services.compatibility import _candidate_pool

# 관리자 픽스처는 T-E01 테스트의 것을 그대로 쓴다(T-E03 과 같은 이유).
from tests.test_admin_rbac import _header, audit_logs, make_admin  # noqa: F401

_REASON = "후보 없음 신고 확인"


async def _seeded_population(make_user, db):
    """하드필터 사유가 골고루 섞인 회원 무리. 여러 테스트가 같은 모양을 쓴다."""
    target = await make_user(
        kakao_id="target",
        gender="male",
        nickname="대상자",
        pref_age_min=25,
        pref_age_max=35,
        pref_region="서울",
        pref_height_min=160,
    )
    good = await make_user(kakao_id="good", gender="female", nickname="통과", region="서울")
    same_gender = await make_user(kakao_id="same", gender="male", nickname="동성")
    blocked_by_target = await make_user(kakao_id="blk", gender="female", nickname="차단됨")
    inactive = await make_user(
        kakao_id="ina", gender="female", nickname="비활성", status=STATUS_INACTIVE
    )
    hidden = await make_user(
        kakao_id="hid", gender="female", nickname="노출중단", profile_hidden=True
    )
    no_birth = await make_user(
        kakao_id="nob", gender="female", nickname="생일없음", birth_date=None
    )
    no_photo = await make_user(
        kakao_id="nop", gender="female", nickname="사진없음", photo_url=None
    )
    db.add(UserBlock(blocker_id=target.id, blocked_id=blocked_by_target.id))
    await db.commit()
    return {
        "target": target,
        "good": good,
        "same_gender": same_gender,
        "blocked": blocked_by_target,
        "inactive": inactive,
        "hidden": hidden,
        "no_birth": no_birth,
        "no_photo": no_photo,
    }


# --- 조건 판정 -----------------------------------------------------------------


async def test_hard_failures_reports_every_violated_code(db, make_user):
    """사유가 여러 개면 전부 나오고, 순서는 서버가 고정한다."""
    people = await _seeded_population(make_user, db)
    target = people["target"]
    worst = await make_user(
        kakao_id="worst",
        gender="male",
        status=STATUS_BLOCKED,
        profile_hidden=True,
        birth_date=None,
        photo_url=None,
    )

    codes = hard_failures(target, worst, frozenset())
    assert codes == [
        HARD_GENDER,
        HARD_STATUS,
        HARD_PROFILE_HIDDEN,
        HARD_NO_BIRTH_DATE,
        HARD_NO_PHOTO,
    ]
    # 최초 탈락 조건은 첫 원소다.
    assert codes[0] == HARD_GENDER


async def test_hard_failures_is_empty_for_a_valid_candidate(db, make_user):
    people = await _seeded_population(make_user, db)
    assert hard_failures(people["target"], people["good"], frozenset()) == []


async def test_self_is_reported_as_self(db, make_user):
    """본인은 본인의 후보가 아니다. 최초 탈락 조건이 성별이 아니라 본인이어야 한다."""
    people = await _seeded_population(make_user, db)
    target = people["target"]
    assert hard_failures(target, target, frozenset())[0] == HARD_SELF


# --- 엔진과의 일치 (이 파일의 핵심) ----------------------------------------------


async def test_verification_agrees_with_engine_pool(client, db, make_user, make_admin):
    """화면의 조건 판정과 엔진의 후보군 SQL 이 같은 답을 내는가.

    어긋나는 순간 두 숫자가 0이 아니게 되고, 그것이 곧 화면에 뜨는 경고다
    (ADM-MATCH-002: 하드필터 위반 포함 0건).
    """
    people = await _seeded_population(make_user, db)
    admin = await make_admin()

    body = (
        await client.get(
            f"/admin/matching/{people['target'].id}", headers=_header(admin)
        )
    ).json()

    assert body["hard_filter_violations"] == 0
    assert body["unexpected_exclusions"] == 0

    engine_ids = {c.id for c in await _candidate_pool(people["target"], db)}
    assert {c["id"] for c in body["candidates"]} == engine_ids
    assert body["pool_count"] == len(engine_ids)


async def test_every_hard_filter_violation_stays_out_of_candidates(
    client, db, make_user, make_admin
):
    """위반 사유가 있는 회원은 하나도 후보 목록에 없고, 전부 제외 목록에 사유와 함께 있다."""
    people = await _seeded_population(make_user, db)
    admin = await make_admin()

    body = (
        await client.get(
            f"/admin/matching/{people['target'].id}", headers=_header(admin)
        )
    ).json()

    candidate_ids = {c["id"] for c in body["candidates"]}
    excluded = {row["id"]: row for row in body["excluded"]}

    expected = {
        people["same_gender"].id: HARD_GENDER,
        people["blocked"].id: HARD_BLOCK,
        people["inactive"].id: HARD_STATUS,
        people["hidden"].id: HARD_PROFILE_HIDDEN,
        people["no_birth"].id: HARD_NO_BIRTH_DATE,
        people["no_photo"].id: HARD_NO_PHOTO,
    }
    for member_id, first_code in expected.items():
        assert member_id not in candidate_ids
        assert excluded[member_id]["first_code"] == first_code

    assert candidate_ids == {people["good"].id}
    # 대상자 본인은 제외 목록을 채우지 않는다 — 모든 대상자에게 항상 붙는 줄이라
    # 실제로 봐야 할 사유를 밀어낸다.
    assert people["target"].id not in excluded


async def test_excluded_rows_carry_all_codes_not_just_the_first(
    client, db, make_user, make_admin
):
    people = await _seeded_population(make_user, db)
    admin = await make_admin()
    both = await make_user(
        kakao_id="both", gender="male", nickname="둘다", status=STATUS_INACTIVE
    )

    body = (
        await client.get(
            f"/admin/matching/{people['target'].id}", headers=_header(admin)
        )
    ).json()
    row = next(r for r in body["excluded"] if r["id"] == both.id)
    assert row["first_code"] == HARD_GENDER
    assert row["codes"] == [HARD_GENDER, HARD_STATUS]


# --- 선호 조건과 완화 단계 -------------------------------------------------------


async def test_soft_checks_carry_values_not_just_pass_fail(
    client, db, make_user, make_admin
):
    """근거는 값과 함께 나와야 재현할 수 있다."""
    people = await _seeded_population(make_user, db)
    admin = await make_admin()

    body = (
        await client.get(
            f"/admin/matching/{people['target'].id}", headers=_header(admin)
        )
    ).json()
    checks = {c["code"]: c for c in body["candidates"][0]["soft_checks"]}
    assert checks["REGION"]["passed"] is True
    assert "서울" in checks["REGION"]["detail"]
    assert "희망 160cm 이상" in checks["HEIGHT"]["detail"]


async def test_relaxation_stage_is_shown_when_preferences_exclude_everyone(
    client, db, make_user, make_admin
):
    """선호 조건을 만족하는 후보가 없으면 엔진은 조건을 완화한다.

    그 사실이 화면에 드러나야 한다 — 운영자가 "선호대로 추천되고 있다"고 오해하면
    OI-MATCH-003(조건 완화는 동의를 받는다)이 지켜지는지 확인할 방법이 없다.
    """
    admin = await make_admin()
    target = await make_user(
        kakao_id="picky",
        gender="male",
        pref_age_min=20,
        pref_age_max=25,
        pref_region="서울",
        pref_height_min=190,
    )
    await make_user(
        kakao_id="far", gender="female", region="부산", height_cm=160,
        birth_date=date(1980, 1, 1),
    )

    body = (
        await client.get(f"/admin/matching/{target.id}", headers=_header(admin))
    ).json()
    assert body["pool_count"] == 1
    assert body["applied_stage_index"] > 0
    applied = body["stages"][body["applied_stage_index"]]
    assert applied["applied"] is True and applied["match_count"] == 1
    assert body["stages"][0]["match_count"] == 0


# --- 대상자 목록 ----------------------------------------------------------------


async def test_list_puts_problem_targets_first(client, db, make_user, make_admin):
    """오류 · 후보 없음 · 정상 순. 문제를 찾으려고 페이지를 넘기지 않아도 된다."""
    admin = await make_admin()
    lonely = await make_user(kakao_id="lonely", gender="female", nickname="후보없음")
    normal_a = await make_user(kakao_id="a", gender="male", nickname="정상A")
    broken = await make_user(kakao_id="broken", gender=None, nickname="성별없음")

    items = (
        await client.get("/admin/matching", headers=_header(admin))
    ).json()["items"]
    order = [i["id"] for i in items]

    assert order[0] == broken.id
    assert items[0]["state"] == "error"
    assert items[0]["issues"]
    # 여성 회원이 둘(lonely, broken)이라 남성 대상자에게는 후보가 있고,
    # 여성 대상자에게는 남성 후보가 하나 있다. 후보가 0인 것은 성별 없는 대상자뿐이
    # 아니므로 상태만 확인한다.
    assert {i["state"] for i in items} <= {"error", "no_candidate", "ok"}
    assert order.index(broken.id) < order.index(normal_a.id)
    assert lonely.id in order


async def test_list_state_filter_and_counts(client, db, make_user, make_admin):
    admin = await make_admin()
    await make_user(kakao_id="noc", gender="female", nickname="혼자")

    body = (
        await client.get("/admin/matching?state=no_candidate", headers=_header(admin))
    ).json()
    assert body["total"] == 1
    assert body["items"][0]["pool_count"] == 0
    assert body["items"][0]["state"] == "no_candidate"


async def test_list_search_matches_member_screen(client, db, make_user, make_admin):
    """회원 화면에서 찾은 회원을 여기서도 같은 방법으로 찾을 수 있어야 한다."""
    admin = await make_admin()
    target = await make_user(kakao_id="s1", nickname="민지", gender="female")
    await make_user(kakao_id="s2", nickname="수현", gender="female")

    by_name = (
        await client.get("/admin/matching?q=민지", headers=_header(admin))
    ).json()
    assert [i["id"] for i in by_name["items"]] == [target.id]

    by_id = (
        await client.get(f"/admin/matching?q={target.id}", headers=_header(admin))
    ).json()
    assert [i["id"] for i in by_id["items"]] == [target.id]


async def test_preferred_count_is_separate_from_pool_count(
    client, db, make_user, make_admin
):
    """선호 조건까지 만족하는 후보 수는 하드필터 통과 수와 다르다."""
    admin = await make_admin()
    target = await make_user(kakao_id="t", gender="male", pref_region="서울")
    await make_user(kakao_id="seoul", gender="female", region="서울")
    await make_user(kakao_id="busan", gender="female", region="부산")

    item = next(
        i
        for i in (
            await client.get("/admin/matching", headers=_header(admin))
        ).json()["items"]
        if i["id"] == target.id
    )
    assert item["pool_count"] == 2
    assert item["preferred_count"] == 1


# --- 열람 이력 ------------------------------------------------------------------


async def test_unlocked_candidates_are_counted_out_of_available(
    client, db, make_user, make_admin
):
    """이미 열람한 후보는 하드필터를 통과하지만 다음 카드로는 나오지 않는다."""
    from app.models.card_unlock import KIND_DAILY, CardUnlock

    admin = await make_admin()
    target = await make_user(kakao_id="t", gender="male")
    seen = await make_user(kakao_id="seen", gender="female")
    db.add(CardUnlock(user_id=target.id, candidate_id=seen.id, kind=KIND_DAILY))
    await db.commit()

    body = (
        await client.get(f"/admin/matching/{target.id}", headers=_header(admin))
    ).json()
    assert body["pool_count"] == 1
    assert body["unlocked_count"] == 1
    assert body["available_count"] == 0
    assert body["candidates"][0]["already_unlocked"] is True


# --- 권한 · 감사 로그 ------------------------------------------------------------


async def test_requires_admin_token(client, make_user):
    from app.core.security import create_access_token

    user = await make_user()
    assert (await client.get("/admin/matching")).status_code == 401
    res = await client.get(
        "/admin/matching",
        headers={"Authorization": f"Bearer {create_access_token(user.id)}"},
    )
    assert res.status_code == 401


async def test_viewer_reads_but_cannot_recalculate(client, make_user, make_admin):
    viewer = await make_admin(role=ROLE_VIEWER)
    user = await make_user()
    headers = _header(viewer)

    assert (await client.get("/admin/matching", headers=headers)).status_code == 200
    assert (
        await client.get(f"/admin/matching/{user.id}", headers=headers)
    ).status_code == 200
    res = await client.post(
        f"/admin/matching/{user.id}/recalculate",
        json={"reason": _REASON},
        headers=headers,
    )
    assert res.status_code == 403


async def test_recalculate_is_audited(client, make_user, make_admin, audit_logs):
    """비용이 드는 작업이라 누가 왜 돌렸는지 남는다 (감사 대상 표 B-2)."""
    admin = await make_admin()
    target = await make_user(kakao_id="t", gender="male")
    await make_user(kakao_id="c", gender="female")

    res = await client.post(
        f"/admin/matching/{target.id}/recalculate",
        json={"reason": _REASON},
        headers=_header(admin),
    )
    assert res.status_code == 200
    assert res.json()["pool_count"] == 1

    log = (await audit_logs())[-1]
    assert log.menu == "매칭"
    assert log.action == "후보재계산"
    assert log.target_id == str(target.id)
    assert log.reason == _REASON
    assert log.after["pool_count"] == 1


async def test_recalculate_requires_a_reason(client, make_user, make_admin):
    admin = await make_admin()
    user = await make_user()
    res = await client.post(
        f"/admin/matching/{user.id}/recalculate",
        json={"reason": ""},
        headers=_header(admin),
    )
    assert res.status_code == 422


async def test_withdrawn_member_is_404(client, make_admin):
    """탈퇴는 행이 지워진다 — 검증할 대상 자체가 없다."""
    admin = await make_admin()
    res = await client.get("/admin/matching/999999", headers=_header(admin))
    assert res.status_code == 404


async def test_verification_does_not_leak_masked_fields(client, make_user, make_admin):
    """매칭 검증이 개인정보 열람 우회 통로가 되지 않아야 한다."""
    admin = await make_admin()
    target = await make_user(kakao_id="kakao-secret-123", gender="male")
    await make_user(kakao_id="cand-secret-456", gender="female", birth_date=date(1994, 2, 3))

    raw = (
        await client.get(f"/admin/matching/{target.id}", headers=_header(admin))
    ).text
    assert "kakao-secret-123" not in raw
    assert "cand-secret-456" not in raw
    assert "1994-02-03" not in raw
