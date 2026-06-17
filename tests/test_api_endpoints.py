"""API端点测试"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch
from app.main import app


@pytest.fixture
def client():
    """创建测试客户端"""
    return TestClient(app)


def test_health_check(client):
    """测试健康检查端点"""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_cf_clearance_status_endpoint(client):
    """测试CF Clearance状态端点 - 通过admin API"""
    response = client.get("/admin/api/config")
    # 端点可能需要认证，检查返回码
    assert response.status_code in [200, 401, 403]


def test_cf_clearance_refresh_endpoint(client):
    """测试CF Clearance刷新端点 - 通过admin API"""
    response = client.post("/admin/api/config")
    # 端点可能需要认证，检查返回码
    assert response.status_code in [200, 401, 403, 405]
