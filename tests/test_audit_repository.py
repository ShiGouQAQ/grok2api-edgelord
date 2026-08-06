"""Audit repository + hot-path recording tests."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app as main_app
from app.platform.audit.model import AuditAttempt, AuditRecord
from app.platform.audit.repository import AuditRepository


@pytest.fixture
def audit_repo(tmp_path):
    repo = AuditRepository(tmp_path / "audits.db")
    asyncio.run(repo.initialize())
    return repo


def _record(
    request_id: str,
    *,
    status_code=200,
    total=30,
    model="grok-4.20-auto",
    created_at=None,
):
    rec = AuditRecord(
        request_id=request_id,
        model=model,
        provider="grok_web",
        operation="chat",
        status_code=status_code,
        input_tokens=10,
        output_tokens=20,
        total_tokens=total,
        duration_ms=7,
        error_code="" if status_code < 300 else "upstream_error",
        attempts=[
            AuditAttempt(
                method="POST",
                request_path="/v1/chat/completions",
                started_at=1,
                duration_ms=7,
            )
        ],
    )
    if created_at is not None:
        rec.created_at = created_at
    return rec


# ═══════════════════════════════════════════════════════════════════════════
# Repository
# ═══════════════════════════════════════════════════════════════════════════


class TestAuditRepository:
    def test_record_and_get_with_attempts(self, audit_repo):
        rec_id = asyncio.run(audit_repo.record(_record("r1")))
        got = asyncio.run(audit_repo.get(rec_id))
        assert got is not None
        assert got.request_id == "r1"
        assert got.model == "grok-4.20-auto"
        assert got.status_code == 200
        assert got.total_tokens == 30
        assert len(got.attempts) == 1
        assert got.attempts[0].request_path == "/v1/chat/completions"

    def test_get_missing_returns_none(self, audit_repo):
        assert asyncio.run(audit_repo.get(9999)) is None

    def test_list_pagination_and_filters(self, audit_repo):
        asyncio.run(audit_repo.record(_record("r1", status_code=200)))
        asyncio.run(audit_repo.record(_record("r2", status_code=502)))
        items, total = asyncio.run(audit_repo.list_records(page=1, page_size=10))
        assert total == 2 and len(items) == 2
        items, total = asyncio.run(audit_repo.list_records(status="failed"))
        assert total == 1 and items[0].request_id == "r2"
        items, total = asyncio.run(audit_repo.list_records(search="r2"))
        assert total == 1
        items, total = asyncio.run(audit_repo.list_records(page=1, page_size=1))
        assert len(items) == 1 and total == 2

    def test_list_cursor_keyset_pagination(self, audit_repo):
        for i in range(3):
            asyncio.run(audit_repo.record(_record(f"r{i}")))
        page1, next_cursor, has_more = asyncio.run(
            audit_repo.list_cursor(page_size=2, period="24h")
        )
        assert len(page1) == 2 and has_more and next_cursor
        page2, next_cursor2, has_more2 = asyncio.run(
            audit_repo.list_cursor(cursor=next_cursor, page_size=2, period="24h")
        )
        assert len(page2) == 1 and not has_more2 and not next_cursor2
        seen = {r.request_id for r in page1 + page2}
        assert seen == {"r0", "r1", "r2"}

    def test_cursor_respects_period(self, audit_repo):
        from app.platform.runtime.clock import now_ms

        asyncio.run(
            audit_repo.record(_record("old", created_at=now_ms() - 30 * 24 * 3600_000))
        )
        asyncio.run(audit_repo.record(_record("new")))
        items, _, _ = asyncio.run(audit_repo.list_cursor(page_size=10, period="24h"))
        assert [r.request_id for r in items] == ["new"]

    def test_summary_aggregates(self, audit_repo):
        asyncio.run(audit_repo.record(_record("s1", status_code=200)))
        asyncio.run(audit_repo.record(_record("s2", status_code=502, total=70)))
        summary = asyncio.run(audit_repo.summary(period="24h"))
        assert summary["requests"] == 2
        assert summary["successfulRequests"] == 1
        assert summary["failedRequests"] == 1
        assert summary["totalTokens"] == 100
        assert summary["successRate"] == 50.0
        assert summary["averageDurationMs"] == 7.0

    def test_summary_invalid_period_raises(self, audit_repo):
        with pytest.raises(ValueError):
            asyncio.run(audit_repo.summary(period="1y"))

    def test_dashboard_aggregate(self, audit_repo):
        asyncio.run(audit_repo.record(_record("d1", total=30, model="grok-4.20-auto")))
        asyncio.run(
            audit_repo.record(
                _record("d2", total=70, model="grok-4.3-fast", status_code=500)
            )
        )
        agg = asyncio.run(audit_repo.dashboard_aggregate("24h"))
        assert agg["usage"]["requests"] == 2
        assert agg["usage"]["successful"] == 1
        assert agg["usage"]["total_tokens"] == 100
        assert len(agg["series"]) == 1
        assert agg["series"][0]["requests"] == 2
        assert [m["model"] for m in agg["topModels"]] == [
            "grok-4.20-auto",
            "grok-4.3-fast",
        ]
        assert agg["topModels"][0]["requests"] == 1


# ═══════════════════════════════════════════════════════════════════════════
# Hot-path recording (router hook)
# ═══════════════════════════════════════════════════════════════════════════


class TestRouterRecording:
    @pytest.fixture(autouse=True)
    def _wire_state(self, audit_repo, monkeypatch):
        main_app.state.audit_repo = audit_repo
        monkeypatch.setattr(
            "app.platform.auth.middleware.get_config", lambda *a, **k: ""
        )
        yield
        main_app.state.audit_repo = None

    def test_chat_completions_records_usage(self):
        from app.products.openai import chat

        async def fake_chat(**kwargs):
            return {
                "choices": [{"message": {"role": "assistant", "content": "hi"}}],
                "usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 9,
                    "total_tokens": 14,
                },
            }

        with patch("app.products.openai.router.chat_completions", side_effect=fake_chat):
            client = TestClient(main_app)
            resp = client.post(
                "/v1/chat/completions",
                json={
                    "model": "grok-4.20-fast",
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": False,
                },
            )
        assert resp.status_code == 200

        import time

        for _ in range(50):  # 后台任务落库
            items, total = asyncio.run(main_app.state.audit_repo.list_records())
            if total:
                break
            time.sleep(0.02)
        assert total == 1
        record = items[0]
        assert record.model == "grok-4.20-fast"
        assert record.operation == "chat"
        assert record.input_tokens == 5
        assert record.output_tokens == 9
        assert record.total_tokens == 14
        assert record.status_code == 200
        assert record.attempt_count == 1

    def test_failed_request_records_error(self):
        from app.products.openai import chat
        from app.platform.errors import UpstreamError

        async def fail(**kwargs):
            raise UpstreamError(
                "upstream 502", status=502, upstream_code="upstream_error"
            )

        with patch("app.products.openai.router.chat_completions", side_effect=fail):
            client = TestClient(main_app)
            resp = client.post(
                "/v1/chat/completions",
                json={
                    "model": "grok-4.20-fast",
                    "messages": [{"role": "user", "content": "hi"}],
                    "stream": False,
                },
            )
        assert resp.status_code == 502

        import time

        for _ in range(50):
            items, total = asyncio.run(main_app.state.audit_repo.list_records())
            if total:
                break
            time.sleep(0.02)
        assert total == 1
        assert items[0].status_code == 502
        assert items[0].error_code == "upstream_error"
