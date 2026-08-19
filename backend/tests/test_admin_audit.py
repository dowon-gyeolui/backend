"""관리자 감사 로그 화면 (T-E08) — ADM-LOG-001 의 QA 체크포인트를 고정한다.

완료 조건 두 가지가 이 파일의 뼈대다.

1. **중요 액션 누락 여부 확인** — 두 방향에서 본다.
   - 코드가 남기는 액션과 화면 사전(`ACTION_CATALOG`)이 정확히 일치하는가
     (`test_catalog_matches_recorded_actions`). 한쪽만 늘어나면 "기록은 되는데
     필터에 없어서 못 찾는" 액션이나 "필터엔 있는데 아무도 안 남기는" 액션이 생긴다.
   - `/admin` 아래 **모든 쓰기 엔드포인트**가 기록을 남기는가
     (`test_every_admin_write_endpoint_records_an_action`). 새 화면이 감사 로그를
     잊고 들어오는 경로를 여기서 막는다. 이것이 실제 "누락"이다.
   - B-2 표의 항목이 하나도 빠지지 않았는가(`test_b2_catalog_is_accounted_for`).
     아직 기능이 없는 항목은 `PENDING_ACTIONS` 에 이유와 함께 남는다.
2. **PII 마스킹 유지** — 저장 시점 마스킹을 우회해 들어온 행이 있어도 화면에는
   원문이 나오지 않는다(`test_raw_row_is_masked_on_read`). 저장 마스킹은
   `test_audit_log.py` 가 이미 고정하므로, 여기서는 **읽는 쪽**을 본다.

삭제 기능이 없다는 것도 여기서 고정한다 — 라우팅에도(`test_router_exposes_read_only_endpoints`)
서비스 모듈에도(`test_no_mutation_path_exists`) 지우는 경로가 없어야 한다.
"""

import ast
import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.core.admin_rbac import ROLE_VIEWER
from app.main import app
from app.models.audit_log import AuditLog
from app.services import admin_audit

# 관리자 픽스처는 T-E01 테스트의 것을 그대로 쓴다(T-E03·T-E04·T-E06·T-E07 과 같은 이유).
from tests.test_admin_rbac import _header, make_admin  # noqa: F401

def _router_dir() -> Path:
    import app.routers

    return Path(app.routers.__file__).parent


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture
def make_log(db):
    """감사 로그 한 줄을 **직접** 넣는다.

    `record_admin_action` 을 태우지 않는 것이 의도다. 그 함수는 호출자 세션을 쓰지
    않아 별도 DB 로 들어가고(`tests/test_admin_rbac.audit_logs` 픽스처), 그러면
    조회 API 가 읽는 세션에서는 보이지 않는다. 여기서 보려는 것은 **읽는 쪽**이라
    행을 요청 세션에 직접 넣는다. 덕분에 마스킹을 우회한 행도 만들어 볼 수 있다.
    """
    seq = 0

    async def _make(**kwargs):
        nonlocal seq
        seq += 1
        defaults = dict(
            admin_id=1,
            menu="회원",
            target_type="user",
            target_id=str(seq),
            action="상태변경",
            before=None,
            after=None,
            reason=None,
            ip="10.0.0.1",
            created_at=_utcnow() - timedelta(minutes=seq),
        )
        defaults.update(kwargs)
        log = AuditLog(**defaults)
        db.add(log)
        await db.commit()
        await db.refresh(log)
        return log

    return _make


# --- 중요 액션 누락 여부 --------------------------------------------------------


def _module_constants(tree: ast.Module) -> dict[str, str]:
    """모듈 최상단의 문자열 상수(`_MENU = "회원"`). 호출부가 이름으로 참조한다."""
    consts: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            if isinstance(target, ast.Name) and isinstance(node.value, ast.Constant):
                if isinstance(node.value.value, str):
                    consts[target.id] = node.value.value
    return consts


def _literals(node: ast.AST, consts: dict[str, str]) -> set[str]:
    """인자에서 나올 수 있는 문자열 값 전부.

    `"프로필노출중단" if data.hidden else "프로필노출재개"` 처럼 한 호출이 두 액션을
    남기는 자리가 있어서, 조건식은 양쪽을 모두 모은다.
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return {node.value}
    if isinstance(node, ast.Name):
        return {consts[node.id]} if node.id in consts else set()
    if isinstance(node, ast.IfExp):
        return _literals(node.body, consts) | _literals(node.orelse, consts)
    return set()


def _record_calls() -> list[tuple[str, ast.Call, dict[str, str]]]:
    calls = []
    for path in sorted(_router_dir().glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        consts = _module_constants(tree)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "record_admin_action"
            ):
                calls.append((path.name, node, consts))
    return calls


def _recorded_pairs() -> set[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for filename, call, consts in _record_calls():
        kwargs = {kw.arg: kw.value for kw in call.keywords}
        menus = _literals(kwargs.get("menu"), consts) if "menu" in kwargs else set()
        actions = _literals(kwargs.get("action"), consts) if "action" in kwargs else set()
        assert menus, f"{filename}: menu 값을 정적으로 읽지 못했다"
        assert actions, f"{filename}: action 값을 정적으로 읽지 못했다"
        pairs |= {(menu, action) for menu in menus for action in actions}
    return pairs


def test_catalog_matches_recorded_actions():
    """코드가 남기는 (메뉴, 액션) 과 화면 사전이 정확히 같아야 한다.

    사전에만 있으면 아무도 안 남기는 필터가 되고, 코드에만 있으면 화면에서 그 액션을
    고를 수 없다. 어느 쪽이든 감사 화면이 조용히 거짓말을 하게 된다.
    """
    catalog = {(meta.menu, meta.action) for meta in admin_audit.ACTION_CATALOG}
    assert _recorded_pairs() == catalog


def test_catalog_has_no_duplicates_and_describes_every_action():
    pairs = [(meta.menu, meta.action) for meta in admin_audit.ACTION_CATALOG]
    assert len(pairs) == len(set(pairs))
    assert all(meta.description.strip() for meta in admin_audit.ACTION_CATALOG)
    assert set(admin_audit.MENUS) == {menu for menu, _ in pairs}


def test_every_admin_write_endpoint_records_an_action():
    """새 관리자 쓰기 엔드포인트가 감사 로그를 잊고 들어오는 것을 막는다."""
    missing = []
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = getattr(route, "methods", set()) or set()
        if not path.startswith("/admin"):
            continue
        if not methods & {"POST", "PUT", "PATCH", "DELETE"}:
            continue
        source = inspect.getsource(route.endpoint)
        if "record_admin_action" not in source:
            missing.append(f"{sorted(methods)} {path}")
    assert missing == [], f"감사 로그를 남기지 않는 쓰기 엔드포인트: {missing}"


def test_b2_catalog_is_accounted_for():
    """B-2(감사 로그 대상, OI-LOG-001 확정) 표의 항목이 하나도 증발하지 않았다.

    기능이 아직 없는 항목은 `PENDING_ACTIONS` 에 이유와 함께 남는다. 목록에서 빼면
    "빠뜨렸다"와 "아직 없다"가 구분되지 않는다.
    """
    covered = {
        "관리자 로그인 성공·실패·로그아웃": [
            ("관리자인증", "로그인"),
            ("관리자인증", "로그인실패"),
            ("관리자인증", "로그아웃"),
        ],
        "회원 상태 변경": [("회원", "상태변경")],
        "프로필 노출 중단·재개": [
            ("회원", "프로필노출중단"),
            ("회원", "프로필노출재개"),
        ],
        "민감정보 원문 열람": [("회원", "민감정보열람")],
        "재화 운영 지급·회수": [("재화", "지급"), ("재화", "회수")],
        "결제 지급 재처리": [("결제", "지급재처리")],
        "후보 재계산 요청": [("매칭", "후보재계산")],
    }
    catalog = {(meta.menu, meta.action) for meta in admin_audit.ACTION_CATALOG}
    for item, pairs in covered.items():
        for pair in pairs:
            assert pair in catalog, f"B-2 '{item}' 의 {pair} 가 사전에 없다"

    pending = {name for name, _ in admin_audit.PENDING_ACTIONS}
    assert pending == {
        "회원 정보 수정",
        "매칭 카드 비활성화·수동 매칭",
        "CSV 등 내보내기",
    }
    assert all(reason.strip() for _, reason in admin_audit.PENDING_ACTIONS)


def test_every_recorded_field_has_a_label():
    """before/after 에 들어가는 키가 화면에 컬럼명 그대로 뜨지 않게 한다."""
    unlabeled = set()
    for _, call, _consts in _record_calls():
        for kw in call.keywords:
            if kw.arg not in {"before", "after"} or not isinstance(kw.value, ast.Dict):
                continue
            for key in kw.value.keys:
                assert isinstance(key, ast.Constant) and isinstance(key.value, str)
                if key.value not in admin_audit.FIELD_LABELS:
                    unlabeled.add(key.value)
    assert unlabeled == set(), f"한국어 라벨이 없는 감사 로그 항목: {sorted(unlabeled)}"


# --- 삭제·수정 경로 없음 --------------------------------------------------------


def test_router_exposes_read_only_endpoints():
    methods = {
        method
        for route in app.routes
        if getattr(route, "path", "").startswith("/admin/audit")
        for method in (getattr(route, "methods", set()) or set())
    }
    assert methods <= {"GET", "HEAD"}, f"감사 로그에 쓰기 경로가 생겼다: {methods}"


def test_no_mutation_path_exists():
    """서비스 모듈에 지우거나 고치는 이름이 생기면 여기서 걸린다."""
    forbidden = ("delete", "remove", "purge", "truncate", "update", "edit")
    names = [name for name in dir(admin_audit) if not name.startswith("_")]
    assert not [
        name for name in names if any(word in name.lower() for word in forbidden)
    ]


async def test_delete_and_patch_are_not_routed(client, make_admin, make_log):
    admin = await make_admin()
    log = await make_log()
    for method in ("delete", "patch", "put"):
        resp = await getattr(client, method)(
            f"/admin/audit/{log.id}",
            headers=_header(admin),
            **({"json": {}} if method != "delete" else {}),
        )
        assert resp.status_code == 405, method


# --- 권한 ------------------------------------------------------------------------


async def test_viewer_cannot_read_audit_log(client, make_admin):
    viewer = await make_admin(role=ROLE_VIEWER, email="viewer-audit@example.com")
    resp = await client.get("/admin/audit", headers=_header(viewer))
    assert resp.status_code == 403


async def test_no_token_is_rejected(client):
    assert (await client.get("/admin/audit")).status_code == 401


async def test_app_user_token_is_rejected(client, make_user):
    """앱 사용자 토큰으로는 관리자 화면이 열리지 않는다(scope 경계)."""
    from app.core.security import create_access_token

    user = await make_user(kakao_id="audit_app_user")
    resp = await client.get(
        "/admin/audit", headers={"Authorization": f"Bearer {create_access_token(user.id)}"}
    )
    assert resp.status_code == 401


# --- 마스킹 유지 ----------------------------------------------------------------


async def test_raw_row_is_masked_on_read(client, make_admin, make_log):
    """저장 마스킹을 우회해 들어온 행도 화면에서는 원문이 나오지 않는다."""
    admin = await make_admin()
    log = await make_log(
        action="상태변경",
        before={"phone": "010-1234-5678", "email": "victim@example.com"},
        after={"phone": "010-8765-4321", "birth_date": "1995-03-20"},
    )

    resp = await client.get(f"/admin/audit/{log.id}", headers=_header(admin))
    assert resp.status_code == 200
    body = resp.text
    for raw in ("010-1234-5678", "010-8765-4321", "victim@example.com", "1995-03-20"):
        assert raw not in body, f"원문이 그대로 나왔다: {raw}"

    diff = {row["key"]: row for row in resp.json()["diff"]}
    assert diff["phone"]["before"].endswith("5678")
    assert diff["phone"]["before"].startswith("*")
    assert diff["email"]["before"] == "vi***@example.com"
    assert diff["birth_date"]["after"] == "1995-**-**"


async def test_list_does_not_leak_raw_values(client, make_admin, make_log):
    admin = await make_admin()
    await make_log(after={"phone": "010-1234-5678"})
    resp = await client.get("/admin/audit", headers=_header(admin))
    assert resp.status_code == 200
    assert "010-1234-5678" not in resp.text
    # 목록은 값 자체를 싣지 않는다. 바뀐 항목 이름만 준다.
    assert resp.json()["items"][0]["changed_fields"] == [
        {"key": "phone", "label": "phone"}
    ]


async def test_admin_email_is_masked_in_response(client, make_admin, make_log):
    admin = await make_admin(email="super@melobe.app", name="관리자일")
    await make_log(admin_id=admin.id)
    resp = await client.get("/admin/audit", headers=_header(admin))
    item = resp.json()["items"][0]
    assert item["admin"]["name"] == "관리자일"
    assert item["admin"]["email"] == "su***@melobe.app"
    assert "super@melobe.app" not in resp.text


def test_masking_is_stable_when_applied_twice():
    """읽을 때 다시 마스킹해도 값이 되살아나지 않는다(2차 방어의 전제)."""
    once = admin_audit.masked_payload({"email": "victim@example.com", "phone": "010-1234-5678"})
    twice = admin_audit.masked_payload(once)
    assert twice["email"] == once["email"]
    assert "1234" not in twice["phone"]


# --- diff ------------------------------------------------------------------------


def test_diff_marks_changed_and_unchanged():
    rows = {
        row.key: row
        for row in admin_audit.diff_rows(
            {"status": "active", "profile_hidden": False},
            {"status": "blocked", "profile_hidden": False},
        )
    }
    assert rows["status"].changed is True
    assert (rows["status"].before, rows["status"].after) == ("active", "blocked")
    assert rows["profile_hidden"].changed is False
    assert rows["profile_hidden"].before == "아니오"
    assert rows["status"].label == "회원 상태"


def test_diff_keeps_one_sided_keys():
    """한쪽에만 있는 키가 사라지면 "새로 생긴 값"이 화면에서 증발한다."""
    rows = {row.key: row for row in admin_audit.diff_rows({"a": 1}, {"b": 2})}
    assert rows["a"].after is None and rows["a"].changed is True
    assert rows["b"].before is None and rows["b"].changed is True


def test_diff_handles_missing_and_scalar_payloads():
    assert admin_audit.diff_rows(None, None) == []
    rows = admin_audit.diff_rows(None, ["kakao_id", "birth_date"])
    assert len(rows) == 1 and rows[0].key == "값"
    assert rows[0].after == "kakao_id, birth_date"


def test_unknown_field_keeps_its_key_as_label():
    assert admin_audit.field_label("brand_new_column") == "brand_new_column"


async def test_detail_returns_404_for_missing_log(client, make_admin):
    admin = await make_admin()
    assert (await client.get("/admin/audit/9999", headers=_header(admin))).status_code == 404


# --- 목록 · 검색 -----------------------------------------------------------------


async def test_list_is_newest_first(client, make_admin, make_log):
    admin = await make_admin()
    old = await make_log(created_at=_utcnow() - timedelta(days=2), target_id="10")
    new = await make_log(created_at=_utcnow(), target_id="11")
    resp = await client.get("/admin/audit", headers=_header(admin))
    ids = [item["id"] for item in resp.json()["items"]]
    assert ids.index(new.id) < ids.index(old.id)


async def test_filters_narrow_the_list(client, make_admin, make_log):
    admin = await make_admin()
    other = await make_admin(email="other-audit@example.com", name="관리자둘")
    await make_log(admin_id=admin.id, menu="회원", action="상태변경", target_id="7")
    await make_log(admin_id=other.id, menu="재화", action="지급", target_id="7")
    await make_log(admin_id=other.id, menu="결제", action="지급재처리", target_id="ORD-1")

    async def _ids(query: str) -> list[dict]:
        resp = await client.get(f"/admin/audit?{query}", headers=_header(admin))
        assert resp.status_code == 200
        return resp.json()["items"]

    assert {item["menu"] for item in await _ids("menu=재화")} == {"재화"}
    assert {item["action"] for item in await _ids("action=지급재처리")} == {"지급재처리"}
    assert {item["admin"]["id"] for item in await _ids(f"admin_id={other.id}")} == {other.id}
    assert len(await _ids("target_type=user&target_id=7")) == 2
    assert len(await _ids("q=ORD")) == 1
    # 관리자 이름으로도 찾는다 — "누가 했는지"가 이 화면의 첫 질문이다.
    assert len(await _ids("q=관리자둘")) == 2


async def test_reason_is_searchable(client, make_admin, make_log):
    admin = await make_admin()
    await make_log(reason="고객센터 보상 #1234")
    await make_log(reason="오지급 회수")
    resp = await client.get("/admin/audit?q=1234", headers=_header(admin))
    assert [item["reason"] for item in resp.json()["items"]] == ["고객센터 보상 #1234"]


async def test_date_filter_uses_kst_calendar_days(client, make_admin, make_log):
    """KST 로 8월 19일 09시에 남은 로그는 UTC 로 8월 19일 00시다.

    UTC 로 자르면 그 로그가 "18일"에 걸려 관리자가 말한 날짜와 어긋난다.
    """
    admin = await make_admin()
    kst = timezone(timedelta(hours=9))
    log = await make_log(created_at=datetime(2026, 8, 19, 9, 0, tzinfo=kst))

    inside = await client.get(
        "/admin/audit?from=2026-08-19&to=2026-08-19", headers=_header(admin)
    )
    assert [item["id"] for item in inside.json()["items"]] == [log.id]

    outside = await client.get(
        "/admin/audit?from=2026-08-18&to=2026-08-18", headers=_header(admin)
    )
    assert outside.json()["items"] == []


async def test_unknown_admin_row_is_visible(client, make_admin, make_log):
    """없는 계정으로 온 로그인 실패(`admin_id=0`)가 목록에서 사라지면 안 된다."""
    admin = await make_admin()
    await make_log(
        admin_id=admin_audit.UNKNOWN_ADMIN_ID,
        menu="관리자인증",
        action="로그인실패",
        target_id=None,
        after={"email": "gh***@example.com"},
    )
    resp = await client.get("/admin/audit?admin_id=0", headers=_header(admin))
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["admin"] == {
        "id": 0,
        "name": None,
        "email": None,
        "role": None,
        "is_active": None,
    }


async def test_list_carries_filter_dictionaries(client, make_admin, make_log):
    admin = await make_admin()
    await make_log()
    body = (await client.get("/admin/audit", headers=_header(admin))).json()
    assert body["menus"] == list(admin_audit.MENUS)
    assert {(a["menu"], a["action"]) for a in body["actions"]} == {
        (meta.menu, meta.action) for meta in admin_audit.ACTION_CATALOG
    }
    assert [a["id"] for a in body["admins"]] == [admin.id]


async def test_pagination_reports_total(client, make_admin, make_log):
    admin = await make_admin()
    for _ in range(3):
        await make_log()
    body = (await client.get("/admin/audit?page=2&size=2", headers=_header(admin))).json()
    assert body["total"] == 3
    assert body["page"] == 2 and len(body["items"]) == 1
