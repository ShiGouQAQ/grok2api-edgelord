"""API端点测试"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch
from main import app


@pytest.fixture
def client():
    """创建测试客户端"""
    return TestClient(app)


def test_health_check(client):
    """测试健康检查端点"""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_cf_clearance_status_endpoint(client):
    """测试CF Clearance状态端点"""
    with patch('app.api.v1.cf_monitor.cf_clearance_manager') as mock_manager:
        mock_manager.get_stats.return_value = {
            "enabled": False,
            "cache_valid": False,
            "stats": {
                "total_checks": 0,
                "cache_hits": 0,
                "cache_misses": 0,
                "solver_success": 0,
                "solver_failures": 0
            },
            "hit_rate": 0.0
        }

        response = client.get("/v1/cf-clearance/status")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "data" in data


@pytest.mark.asyncio
async def test_cf_clearance_refresh_endpoint(client):
    """测试CF Clearance刷新端点"""
    with patch('app.api.v1.cf_monitor.cf_clearance_manager') as mock_manager:
        mock_manager.ensure_valid_clearance = AsyncMock(return_value=True)

        response = client.post("/v1/cf-clearance/refresh")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
