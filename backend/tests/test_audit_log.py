"""감사 로그(T-B02) — 마스킹과 "기록 실패가 본 작업을 막지 않는다"를 고정한다.

여기서 막고 싶은 사고는 두 가지다.
1. 관리자 화면이 회원 연락처·생년월일을 그대로 before/after 에 넣어 로그 테이블이
   또 하나의 PII 보관소가 되는 것.
2. 로그 insert 가 실패해서 회원 상태 변경 같은 본 작업까지 같이 죽는 것.
"""

import logging
from datetime import date

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.models.audit_log import AuditLog
from app.services import audit


@pytest_asyncio.fixture
async def record(engine, monkeypatch):
    """`record_admin_action` 이 테스트 DB 를 쓰도록 세션 팩토리를 갈아끼운다.

    서비스가 호출자 세션이 아니라 자기 세션을 여는 게 설계의 핵심이라
    (실패 격리), 테스트도 그 경로를 그대로 태운다.
    """
    monkeypatch.setattr(
        audit, "AsyncSessionLocal", async_sessionmaker(engine, expire_on_commit=False)
    )
    return audit.record_admin_action


@pytest_asyncio.fixture
async def read_logs(engine):
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _read() -> list[AuditLog]:
        async with maker() as session:
            result = await session.execute(select(AuditLog).order_by(AuditLog.id))
            return list(result.scalars())

    return _read


# --- 마스킹 -------------------------------------------------------------------

@pytest.mark.parametrize(
    "key,raw,masked",
    [
        ("phone", "010-1234-5678", "***-****-5678"),
        ("phone_number", "01012345678", "*******5678"),
        ("연락처", "010-1234-5678", "***-****-5678"),
        ("email", "hongildong@example.com", "ho***@example.com"),
        ("email", "ab@example.com", "***@example.com"),
        ("birth_date", "1995-03-20", "1995-**-**"),
        ("생년월일", "1995-03-20", "1995-**-**"),
        ("name", "홍길동", "홍**"),
        ("kakao_id", "1234567890", "1*********"),
        ("birth_place", "서울특별시 강남구", "서********"),
    ],
)
def test_known_pii_keys_are_masked(key, raw, masked):
    assert audit.mask_sensitive({key: raw}) == {key: masked}


@pytest.mark.parametrize(
    "key",
    ["password", "password_hash", "access_token", "kakao_refresh_token", "api_key"],
)
def test_secrets_are_fully_redacted(key):
    """시크릿류는 길이·형태조차 남기지 않는다."""
    assert audit.mask_sensitive({key: "s3cr3t-value"}) == {key: "***"}


def test_non_pii_fields_survive_untouched():
    """가릴 이유가 없는 값까지 가리면 감사 로그가 쓸모없어진다."""
    before = {"status": "정상", "star_balance": 120, "is_paid": True, "nickname": "달빛"}
    assert audit.mask_sensitive(before) == before


def test_masking_walks_nested_structures():
    payload = {
        "user": {"name": "홍길동", "phone": "010-1234-5678"},
        "contacts": [{"email": "aaa@b.com"}, {"email": "ccc@d.com"}],
    }
    assert audit.mask_sensitive(payload) == {
        "user": {"name": "홍**", "phone": "***-****-5678"},
        "contacts": [{"email": "aa***@b.com"}, {"email": "cc***@d.com"}],
    }


def test_non_json_types_become_strings():
    """date 를 그대로 두면 JSON 직렬화에서 터져 로그가 통째로 날아간다."""
    masked = audit.mask_sensitive({"created_at": date(2026, 8, 13), "birth_date": date(1995, 3, 20)})
    assert masked == {"created_at": "2026-08-13", "birth_date": "1995-**-**"}


def test_mask_sensitive_passes_none_through():
    assert audit.mask_sensitive(None) is None


# --- 기록 ---------------------------------------------------------------------

async def test_record_persists_masked_values(record, read_logs):
    log_id = await record(
        admin_id=7,
        menu="회원관리",
        target_type="user",
        target_id=42,
        action="회원정보수정",
        before={"phone": "010-1234-5678", "status": "정상"},
        after={"phone": "010-9999-0000", "status": "비활성"},
        reason="본인 요청",
        ip="203.0.113.9",
    )

    assert log_id is not None
    logs = await read_logs()
    assert len(logs) == 1
    log = logs[0]

    assert (log.admin_id, log.menu, log.target_type, log.target_id) == (
        7, "회원관리", "user", "42",
    )
    assert log.action == "회원정보수정"
    assert log.reason == "본인 요청"
    assert log.ip == "203.0.113.9"
    assert log.created_at is not None

    # 평문 연락처가 어느 컬럼에도 남지 않아야 한다.
    assert log.before == {"phone": "***-****-5678", "status": "정상"}
    assert log.after == {"phone": "***-****-0000", "status": "비활성"}
    assert "1234-5678" not in str(log.before)


async def test_record_masks_even_if_caller_forgets(record, read_logs):
    """마스킹 책임이 호출자에게 있으면 언젠가 빠뜨린다 → 서비스가 강제한다."""
    await record(
        admin_id=1,
        menu="회원관리",
        target_type="user",
        action="민감정보열람",
        after={"email": "hongildong@example.com", "password_hash": "$2b$12$abcdef"},
    )
    log = (await read_logs())[0]
    assert log.after == {"email": "ho***@example.com", "password_hash": "***"}


async def test_record_failure_does_not_raise(monkeypatch, caplog):
    """로그 테이블에 못 써도 예외를 던지지 않는다 — 본 작업이 같이 죽으면 안 된다."""
    broken = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )  # create_all 을 하지 않아 audit_logs 테이블이 없다
    monkeypatch.setattr(
        audit, "AsyncSessionLocal", async_sessionmaker(broken, expire_on_commit=False)
    )
    try:
        with caplog.at_level(logging.ERROR, logger="app.services.audit"):
            result = await audit.record_admin_action(
                admin_id=1, menu="회원관리", target_type="user", action="비활성화"
            )
    finally:
        await broken.dispose()

    assert result is None
    # 조용히 삼키면 로그가 사라진 걸 아무도 모른다. 실패 자체는 반드시 드러나야 한다.
    assert [r for r in caplog.records if r.levelno >= logging.ERROR], "기록 실패가 로깅되지 않았다"


async def test_records_accumulate(record, read_logs):
    await record(admin_id=1, menu="재화", target_type="user", target_id="1", action="지급")
    await record(admin_id=2, menu="재화", target_type="user", target_id="1", action="회수")
    assert [log.action for log in await read_logs()] == ["지급", "회수"]


def test_no_deletion_path_exists():
    """QA 요구: 감사 로그 삭제 기능 금지. 서비스에 삭제 진입점이 생기면 여기서 걸린다."""
    forbidden = [
        name
        for name in dir(audit)
        if any(word in name.lower() for word in ("delete", "remove", "purge", "truncate"))
    ]
    assert not forbidden, f"삭제 경로로 보이는 이름: {forbidden}"


async def test_model_never_declares_ondelete_cascade():
    """다른 테이블이 지워질 때 감사 로그가 딸려 지워지면 안 된다."""
    assert not AuditLog.__table__.foreign_keys
    assert "audit_logs" in Base.metadata.tables
