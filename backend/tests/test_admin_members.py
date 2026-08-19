"""관리자 회원 목록·상세 (T-E03) — QA 체크포인트를 고정한다.

QA 기능정의서 ADM-MEM-001/002 가 요구하는 네 가지가 이 파일의 뼈대다.

1. **동명이인 구분** — 닉네임이 같아도 id·가입일·마스킹된 생년월일로 갈린다.
2. **마스킹 유지** — 목록·상세 어느 경로로도 연락처·생년월일 원문이 나오지 않는다.
3. **새로고침 후 다시 마스킹** — 원문 열람은 그 응답 한 번뿐이고 서버에 상태가 남지 않는다.
4. **탈퇴 회원 처리** — 탈퇴는 hard delete 라 목록에 없고 상세는 404 다.

여기에 RBAC(Viewer 는 조회만) 와 감사 로그(변경·원문 열람)를 얹는다.
"""

from datetime import date, datetime, timedelta, timezone

from app.core.admin_rbac import ROLE_VIEWER
from app.core.security import create_access_token
from app.models.user import STATUS_ACTIVE, STATUS_BLOCKED, STATUS_INACTIVE
from app.services.admin_members import matchable_blockers
from app.services.compatibility import _candidate_pool

# 관리자 픽스처는 T-E01 테스트에 있는 것을 그대로 쓴다. 복사하면 감사 로그 세션 분리
# 같은 미묘한 설정이 두 벌이 되어 한쪽만 낡는다.
from tests.test_admin_rbac import _header, audit_logs, make_admin  # noqa: F401

_REASON = "운영 검토"


async def test_requires_admin_token(client, make_user):
    """앱 사용자 토큰으로는 열리지 않는다. 두 토큰의 id 공간이 겹치기 때문이다."""
    user = await make_user()
    assert (await client.get("/admin/members")).status_code == 401

    res = await client.get(
        "/admin/members",
        headers={"Authorization": f"Bearer {create_access_token(user.id)}"},
    )
    assert res.status_code == 401


async def test_viewer_reads_but_cannot_change(client, make_user, make_admin, audit_logs):
    """UI 를 숨기는 것이 아니라 URL 을 직접 두드려도 막혀야 한다."""
    viewer = await make_admin(role=ROLE_VIEWER)
    user = await make_user()
    headers = _header(viewer)

    assert (await client.get("/admin/members", headers=headers)).status_code == 200
    assert (
        await client.get(f"/admin/members/{user.id}", headers=headers)
    ).status_code == 200

    for path, body in (
        (f"/admin/members/{user.id}/status", {"status": STATUS_BLOCKED, "reason": _REASON}),
        (f"/admin/members/{user.id}/profile-visibility", {"hidden": True, "reason": _REASON}),
        (f"/admin/members/{user.id}/unmask", {"reason": _REASON}),
    ):
        res = await client.post(path, json=body, headers=headers)
        assert res.status_code == 403, path


# --- 목록 ---------------------------------------------------------------------


async def test_list_masks_contact_and_birth_date(client, make_user, make_admin):
    admin = await make_admin()
    await make_user(kakao_id="kakao-9876543", username="minji01", birth_date=date(1995, 3, 20))

    item = (await client.get("/admin/members", headers=_header(admin))).json()["items"][0]
    assert item["kakao_id"] == "k************"
    assert item["username"] == "m******"
    assert item["birth_date"] == "1995-**-**"


async def test_list_is_sorted_by_signup_date_desc(client, make_user, make_admin):
    admin = await make_admin()
    now = datetime.now(timezone.utc)
    old = await make_user(kakao_id="old", created_at=now - timedelta(days=3))
    mid = await make_user(kakao_id="mid", created_at=now - timedelta(days=1))
    new = await make_user(kakao_id="new", created_at=now)

    body = (await client.get("/admin/members", headers=_header(admin))).json()
    assert [i["id"] for i in body["items"]] == [new.id, mid.id, old.id]
    assert body["total"] == 3


async def test_same_nickname_members_are_distinguishable(client, make_user, make_admin):
    """동명이인 QA 체크포인트 — 닉네임만으로는 못 가르니 다른 축이 반드시 있어야 한다."""
    admin = await make_admin()
    now = datetime.now(timezone.utc)
    a = await make_user(
        kakao_id="a", nickname="민지", birth_date=date(1995, 3, 20),
        region="서울", created_at=now - timedelta(days=2),
    )
    b = await make_user(
        kakao_id="b", nickname="민지", birth_date=date(1990, 7, 1),
        region="부산", created_at=now,
    )

    items = (
        await client.get("/admin/members?q=민지", headers=_header(admin))
    ).json()["items"]
    assert [i["id"] for i in items] == [b.id, a.id]
    assert [i["birth_date"] for i in items] == ["1990-**-**", "1995-**-**"]
    assert [i["region"] for i in items] == ["부산", "서울"]
    assert items[0]["created_at"] != items[1]["created_at"]


async def test_search_by_id_finds_one_of_the_namesakes(client, make_user, make_admin):
    admin = await make_admin()
    await make_user(kakao_id="a", nickname="민지")
    b = await make_user(kakao_id="b", nickname="민지")

    items = (
        await client.get(f"/admin/members?q={b.id}", headers=_header(admin))
    ).json()["items"]
    assert [i["id"] for i in items] == [b.id]


async def test_status_filter(client, make_user, make_admin):
    admin = await make_admin()
    await make_user(kakao_id="a")
    blocked = await make_user(kakao_id="b", status=STATUS_BLOCKED)

    items = (
        await client.get(
            f"/admin/members?status={STATUS_BLOCKED}", headers=_header(admin)
        )
    ).json()["items"]
    assert [i["id"] for i in items] == [blocked.id]


async def test_matchable_filter_agrees_with_reasons(client, make_user, make_admin, db):
    """SQL 필터와 상세의 사유 목록이 어긋나면 "목록엔 가능, 상세엔 불가"가 나온다.

    성별 미입력 회원이 핵심이다 — `gender IN (...)` 은 NULL 에서 NULL 이라,
    3값 논리를 다루지 않으면 매칭 불가 목록에서 통째로 사라진다.
    """
    admin = await make_admin()
    ok = await make_user(kakao_id="ok")
    await make_user(kakao_id="nogender", gender=None)
    await make_user(kakao_id="nophoto", photo_url=None)
    await make_user(kakao_id="inactive", status=STATUS_INACTIVE)
    await make_user(kakao_id="hidden", profile_hidden=True)

    async def ids(query: str) -> list[int]:
        res = await client.get(f"/admin/members?{query}", headers=_header(admin))
        return sorted(i["id"] for i in res.json()["items"])

    every = (await client.get("/admin/members", headers=_header(admin))).json()["items"]
    expected_ok = sorted(i["id"] for i in every if not i["matchable_blockers"])

    assert await ids("matchable=true") == expected_ok == [ok.id]
    assert await ids("matchable=false") == sorted(
        i["id"] for i in every if i["matchable_blockers"]
    )
    assert len(every) == 5  # 어느 쪽에서도 빠진 회원이 없다


async def test_blockers_match_the_python_helper(db, make_user):
    hidden = await make_user(kakao_id="hidden", profile_hidden=True)
    assert matchable_blockers(hidden) == ["프로필 노출 중단"]
    assert matchable_blockers(await make_user(kakao_id="ok")) == []


# --- 상세 ---------------------------------------------------------------------


async def test_detail_has_every_tab_and_stays_masked(client, make_user, make_admin):
    admin = await make_admin()
    user = await make_user(
        kakao_id="kakao-9876543", birth_date=date(1995, 3, 20), birth_time="09:30",
        birth_place="서울", nickname="민지",
    )

    body = (await client.get(f"/admin/members/{user.id}", headers=_header(admin))).json()

    assert body["summary"]["matchable"] is True
    assert body["basic"]["birth_date"] == "1995-**-**"
    assert body["basic"]["kakao_id"] == "k************"
    assert body["basic"]["birth_time"] == "0****"
    assert body["basic"]["birth_place"] == "서*"
    assert body["profile"]["nickname"] == "민지"
    # 사주는 기존 엔진(services/saju)이 낸 값이다 — placeholder 가 아니다.
    assert [p["label"] for p in body["saju"]["pillars"]] == ["년주", "월주", "일주", "시주"]
    assert body["saju"]["pillars"][0]["combined"] == "을해"
    assert body["matching"]["daily_count"] == 0
    assert body["billing"]["star_balance"] == 0


async def test_detail_without_birth_date_has_no_saju(client, make_user, make_admin):
    admin = await make_admin()
    user = await make_user(kakao_id="nb", birth_date=None)

    body = (await client.get(f"/admin/members/{user.id}", headers=_header(admin))).json()
    assert body["saju"] is None
    assert "생년월일 미입력" in body["summary"]["matchable_blockers"]


async def test_withdrawn_member_is_gone(client, make_user, make_admin, db):
    """탈퇴 QA 체크포인트 — 탈퇴는 hard delete 라 목록에 없고 상세는 404 다."""
    admin = await make_admin()
    user = await make_user(kakao_id="bye")
    member_id = user.id
    await db.delete(user)
    await db.commit()

    res = await client.get(f"/admin/members/{member_id}", headers=_header(admin))
    assert res.status_code == 404
    assert "탈퇴" in res.json()["detail"]

    body = (await client.get("/admin/members", headers=_header(admin))).json()
    assert body["total"] == 0

    res = await client.post(
        f"/admin/members/{member_id}/status",
        json={"status": STATUS_BLOCKED, "reason": _REASON},
        headers=_header(admin),
    )
    assert res.status_code == 404


# --- 변경 ---------------------------------------------------------------------


async def test_status_change_is_logged_and_removes_from_matching(
    client, make_user, make_admin, audit_logs, db
):
    admin = await make_admin()
    me = await make_user(kakao_id="me", gender="male")
    target = await make_user(kakao_id="target", gender="female")
    assert [c.id for c in await _candidate_pool(me, db)] == [target.id]

    res = await client.post(
        f"/admin/members/{target.id}/status",
        json={"status": STATUS_BLOCKED, "reason": "신고 3회 누적"},
        headers=_header(admin),
    )
    assert res.status_code == 200
    assert res.json()["status"] == STATUS_BLOCKED
    assert res.json()["matchable"] is False

    await db.refresh(target)
    assert target.status == STATUS_BLOCKED
    # 상태 변경이 실제로 효력을 가져야 한다 — 화면 표시만 바뀌면 제재가 아니다.
    assert await _candidate_pool(me, db) == []

    log = [entry for entry in await audit_logs() if entry.action == "상태변경"][0]
    assert log.admin_id == admin.id
    assert log.target_id == str(target.id)
    assert log.before == {"status": STATUS_ACTIVE}
    assert log.after == {"status": STATUS_BLOCKED}
    assert log.reason == "신고 3회 누적"


async def test_status_change_requires_reason(client, make_user, make_admin):
    admin = await make_admin()
    user = await make_user()
    res = await client.post(
        f"/admin/members/{user.id}/status",
        json={"status": STATUS_BLOCKED, "reason": ""},
        headers=_header(admin),
    )
    assert res.status_code == 422


async def test_withdrawal_is_not_a_settable_status(client, make_user, make_admin):
    """탈퇴는 복구 불가 삭제라(OI-MEM-001) 관리자 버튼으로 일어나지 않는다."""
    admin = await make_admin()
    user = await make_user()
    res = await client.post(
        f"/admin/members/{user.id}/status",
        json={"status": "withdrawn", "reason": _REASON},
        headers=_header(admin),
    )
    assert res.status_code == 422


async def test_profile_visibility_change_is_logged_and_effective(
    client, make_user, make_admin, audit_logs, db
):
    admin = await make_admin()
    me = await make_user(kakao_id="me", gender="male")
    target = await make_user(kakao_id="target", gender="female")

    res = await client.post(
        f"/admin/members/{target.id}/profile-visibility",
        json={"hidden": True, "reason": "사진 검수"},
        headers=_header(admin),
    )
    assert res.status_code == 200
    assert res.json()["profile_hidden"] is True
    assert await _candidate_pool(me, db) == []

    res = await client.post(
        f"/admin/members/{target.id}/profile-visibility",
        json={"hidden": False, "reason": "검수 완료"},
        headers=_header(admin),
    )
    assert res.json()["profile_hidden"] is False
    assert [c.id for c in await _candidate_pool(me, db)] == [target.id]

    actions = [entry.action for entry in await audit_logs()]
    assert actions == ["프로필노출중단", "프로필노출재개"]


# --- 민감정보 원문 열람 (OI-MEM-004) -------------------------------------------


async def test_unmask_returns_plaintext_and_logs_without_it(
    client, make_user, make_admin, audit_logs
):
    admin = await make_admin()
    user = await make_user(
        kakao_id="kakao-9876543", username="minji01",
        birth_date=date(1995, 3, 20), birth_time="09:30", birth_place="서울",
    )

    res = await client.post(
        f"/admin/members/{user.id}/unmask",
        json={"reason": "본인 확인 요청 (2026-08-19 CS)"},
        headers=_header(admin),
    )
    assert res.status_code == 200
    assert res.json() == {
        "id": user.id,
        "kakao_id": "kakao-9876543",
        "username": "minji01",
        "birth_date": "1995-03-20",
        "birth_time": "09:30",
        "birth_place": "서울",
    }

    log = (await audit_logs())[0]
    assert log.action == "민감정보열람"
    assert log.reason == "본인 확인 요청 (2026-08-19 CS)"
    # 열람 기록이 원문을 담으면 로그 자체가 두 번째 유출 경로가 된다.
    assert "9876543" not in str(log.after)
    assert "1995-03-20" not in str(log.after)


async def test_unmask_requires_reason(client, make_user, make_admin):
    admin = await make_admin()
    user = await make_user()
    res = await client.post(
        f"/admin/members/{user.id}/unmask", json={}, headers=_header(admin)
    )
    assert res.status_code == 422


async def test_masking_returns_after_reload(client, make_user, make_admin):
    """새로고침 후 다시 마스킹 — 원문 열람은 그 응답 한 번뿐이다."""
    admin = await make_admin()
    user = await make_user(kakao_id="kakao-9876543", birth_date=date(1995, 3, 20))

    await client.post(
        f"/admin/members/{user.id}/unmask", json={"reason": _REASON}, headers=_header(admin)
    )

    body = (await client.get(f"/admin/members/{user.id}", headers=_header(admin))).json()
    assert body["basic"]["kakao_id"] == "k************"
    assert body["basic"]["birth_date"] == "1995-**-**"
