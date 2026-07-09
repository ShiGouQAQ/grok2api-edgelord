import asyncio
import json
import sqlite3
import tempfile
import time
import os
from unittest.mock import patch, MagicMock


def test_cf_clearance_history_table_creation():
    """测试 CF Clearance 历史记录表是否能正确创建"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # 创建表
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cf_clearance_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL NOT NULL,
                event_type TEXT NOT NULL,
                success BOOLEAN NOT NULL,
                duration REAL,
                details TEXT,
                error_message TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 创建索引
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_cf_history_timestamp ON cf_clearance_history(timestamp)"
        )
        cursor.execute(
            "CREATE INDEX IF NOT EXISTS idx_cf_history_event_type ON cf_clearance_history(event_type)"
        )

        conn.commit()

        # 验证表是否存在
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='cf_clearance_history'"
        )
        table = cursor.fetchone()
        assert table is not None
        assert table[0] == "cf_clearance_history"

        # 验证索引是否存在
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_cf_history_timestamp'"
        )
        index = cursor.fetchone()
        assert index is not None

        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_cf_history_event_type'"
        )
        index = cursor.fetchone()
        assert index is not None

        conn.close()
    finally:
        os.unlink(db_path)


def test_proxy_directory_init_database():
    """测试 ProxyDirectory 的 _init_history_database 方法能正确创建表"""
    from app.control.proxy import ProxyDirectory

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        directory = ProxyDirectory.__new__(ProxyDirectory)
        directory._get_history_database_path = lambda: db_path

        directory._init_history_database()

        # 验证表是否创建
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='cf_clearance_history'"
        )
        table = cursor.fetchone()
        assert table is not None
        assert table[0] == "cf_clearance_history"

        # 验证索引
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_cf_history_timestamp'"
        )
        index = cursor.fetchone()
        assert index is not None

        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_cf_history_event_type'"
        )
        index = cursor.fetchone()
        assert index is not None

        conn.close()
    finally:
        os.unlink(db_path)


def test_record_event():
    """测试记录事件功能"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        from app.control.proxy import ProxyDirectory

        directory = ProxyDirectory.__new__(ProxyDirectory)
        directory._get_history_database_path = lambda: db_path
        directory._init_history_database()

        # 记录事件
        asyncio.run(
            directory.record_event(
                event_type="solve",
                success=True,
                duration=1.5,
                details={"node": "test-node"},
            )
        )

        # 验证记录是否保存
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM cf_clearance_history")
        row = cursor.fetchone()
        assert row is not None
        assert row[2] == "solve"  # event_type
        assert row[3] == 1  # success
        assert row[4] == 1.5  # duration
        assert json.loads(row[5]) == {"node": "test-node"}  # details
        conn.close()
    finally:
        os.unlink(db_path)


def test_get_history():
    """测试获取历史记录功能"""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name

    try:
        from app.control.proxy import ProxyDirectory

        directory = ProxyDirectory.__new__(ProxyDirectory)
        directory._get_history_database_path = lambda: db_path
        directory._init_history_database()

        # 插入测试数据
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        for i in range(10):
            cursor.execute(
                "INSERT INTO cf_clearance_history (timestamp, event_type, success, duration) VALUES (?, ?, ?, ?)",
                (time.time(), "solve", i % 2 == 0, 1.0),
            )
        conn.commit()
        conn.close()

        # 获取历史记录
        result = asyncio.run(directory.get_history(page=1, page_size=5))
        assert result["total"] == 10
        assert len(result["items"]) == 5
        assert result["page"] == 1
        assert result["page_size"] == 5
    finally:
        os.unlink(db_path)


def test_cf_clearance_history_endpoint():
    """测试 CF Clearance 历史记录 API 端点"""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.platform.auth.middleware import verify_admin_key

    async def _mock_verify_admin_key():
        return None

    app.dependency_overrides[verify_admin_key] = _mock_verify_admin_key
    try:
        client = TestClient(app)

        # 测试获取历史记录
        response = client.get("/admin/api/cf-clearance/history?page=1&page_size=10")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "total" in data["data"]
        assert "page" in data["data"]
        assert "page_size" in data["data"]
        assert "items" in data["data"]
    finally:
        app.dependency_overrides.pop(verify_admin_key, None)


def test_cf_clearance_stats_endpoint():
    """测试 CF Clearance 统计信息 API 端点"""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.platform.auth.middleware import verify_admin_key

    async def _mock_verify_admin_key():
        return None

    app.dependency_overrides[verify_admin_key] = _mock_verify_admin_key
    try:
        client = TestClient(app)

        # 测试获取统计信息
        response = client.get("/admin/api/cf-clearance/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert "enabled" in data["data"]
        assert "cache_valid" in data["data"]
        assert "stats" in data["data"]
        assert "hit_rate" in data["data"]
    finally:
        app.dependency_overrides.pop(verify_admin_key, None)


def test_cf_clearance_clear_history_endpoint():
    """测试清空历史记录 API 端点"""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.platform.auth.middleware import verify_admin_key

    async def _mock_verify_admin_key():
        return None

    app.dependency_overrides[verify_admin_key] = _mock_verify_admin_key
    try:
        client = TestClient(app)

        # 测试清空历史记录
        response = client.delete("/admin/api/cf-clearance/history")
        assert response.status_code == 200
        data = response.json()
        assert data["success"] == True
    finally:
        app.dependency_overrides.pop(verify_admin_key, None)


def test_full_workflow():
    """测试完整工作流程"""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.platform.auth.middleware import verify_admin_key

    async def _mock_verify_admin_key():
        return None

    app.dependency_overrides[verify_admin_key] = _mock_verify_admin_key
    try:
        client = TestClient(app)

        # 1. 获取统计信息
        response = client.get("/admin/api/cf-clearance/stats")
        assert response.status_code == 200
        stats = response.json()
        assert stats["success"] is True

        # 2. 获取历史记录
        response = client.get("/admin/api/cf-clearance/history?page=1&page_size=10")
        assert response.status_code == 200
        history = response.json()
        assert history["success"] is True

        # 3. 清空历史记录
        response = client.delete("/admin/api/cf-clearance/history")
        assert response.status_code == 200
        clear_result = response.json()
        assert clear_result["success"] is True

        # 4. 验证历史记录已清空
        response = client.get("/admin/api/cf-clearance/history?page=1&page_size=10")
        assert response.status_code == 200
        history_after_clear = response.json()
        assert history_after_clear["data"]["total"] == 0
    finally:
        app.dependency_overrides.pop(verify_admin_key, None)


def _make_directory(db_path):
    from app.control.proxy import ProxyDirectory
    import asyncio
    from unittest.mock import MagicMock

    directory = ProxyDirectory.__new__(ProxyDirectory)
    directory._get_history_database_path = lambda: db_path
    directory._init_history_database()
    directory._stats = {
        "total_checks": 0,
        "cache_hits": 0,
        "cache_misses": 0,
        "solver_success": 0,
        "solver_failures": 0,
        "precheck_skips": 0,
    }
    directory._last_check_time = {}
    directory._check_interval = 3600
    directory._lock = asyncio.Lock()
    directory._clearance_mode = MagicMock()
    directory._clearance_mode.__ne__ = lambda self, other: True
    return directory


def _insert_events(db_path, events):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    for e in events:
        cursor.execute(
            "INSERT INTO cf_clearance_history (timestamp, event_type, success, duration) VALUES (?, ?, ?, ?)",
            (
                e.get("timestamp", time.time()),
                e["event_type"],
                e["success"],
                e.get("duration", 1.0),
            ),
        )
    conn.commit()
    conn.close()


def test_get_stats_empty_db():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        directory = _make_directory(db_path)
        stats = directory.get_stats()
        assert stats["stats"]["total_checks"] == 0
        assert stats["stats"]["solver_success"] == 0
        assert stats["stats"]["solver_failures"] == 0
        assert stats["hit_rate"] == 0.0
        assert stats["enabled"] is True
    finally:
        os.unlink(db_path)


def test_get_stats_mixed_success_failure():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        directory = _make_directory(db_path)
        _insert_events(
            db_path,
            [
                {"event_type": "clearance_refresh", "success": True},
                {"event_type": "clearance_refresh", "success": True},
                {"event_type": "clearance_refresh", "success": True},
                {"event_type": "clearance_refresh", "success": False},
                {"event_type": "clearance_refresh", "success": False},
            ],
        )
        stats = directory.get_stats()
        assert stats["stats"]["total_checks"] == 5
        assert stats["stats"]["solver_success"] == 3
        assert stats["stats"]["solver_failures"] == 2
    finally:
        os.unlink(db_path)


def test_get_stats_only_success():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        directory = _make_directory(db_path)
        _insert_events(
            db_path,
            [
                {"event_type": "clearance_refresh", "success": True},
                {"event_type": "clearance_refresh", "success": True},
            ],
        )
        stats = directory.get_stats()
        assert stats["stats"]["solver_success"] == 2
        assert stats["stats"]["solver_failures"] == 0
        assert stats["stats"]["total_checks"] == 2
    finally:
        os.unlink(db_path)


def test_get_stats_only_failure():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        directory = _make_directory(db_path)
        _insert_events(
            db_path,
            [
                {"event_type": "clearance_refresh", "success": False},
                {"event_type": "clearance_refresh", "success": False},
            ],
        )
        stats = directory.get_stats()
        assert stats["stats"]["solver_success"] == 0
        assert stats["stats"]["solver_failures"] == 2
        assert stats["stats"]["total_checks"] == 2
    finally:
        os.unlink(db_path)


def test_get_stats_filters_non_clearance_events():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        directory = _make_directory(db_path)
        _insert_events(
            db_path,
            [
                {"event_type": "clearance_refresh", "success": True},
                {"event_type": "clearance_refresh", "success": False},
                {"event_type": "other_event", "success": True},
                {"event_type": "precheck_skip", "success": True},
            ],
        )
        stats = directory.get_stats()
        assert stats["stats"]["total_checks"] == 2
        assert stats["stats"]["solver_success"] == 1
        assert stats["stats"]["solver_failures"] == 1
    finally:
        os.unlink(db_path)


def test_get_stats_db_error_graceful():
    from app.control.proxy import ProxyDirectory
    import asyncio
    from unittest.mock import MagicMock

    directory = ProxyDirectory.__new__(ProxyDirectory)
    directory._get_history_database_path = lambda: "/nonexistent/path/db.sqlite"
    directory._stats = {
        "total_checks": 0,
        "cache_hits": 0,
        "cache_misses": 0,
        "solver_success": 0,
        "solver_failures": 0,
        "precheck_skips": 0,
    }
    directory._last_check_time = {}
    directory._check_interval = 3600
    directory._lock = asyncio.Lock()
    directory._clearance_mode = MagicMock()
    directory._clearance_mode.__ne__ = lambda self, other: True

    stats = directory.get_stats()
    assert stats["stats"]["total_checks"] == 0
    assert stats["stats"]["solver_success"] == 0
    assert stats["stats"]["solver_failures"] == 0
    assert stats["enabled"] is True


def test_get_stats_reads_from_db_not_memory():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        db_path = tmp.name
    try:
        directory = _make_directory(db_path)
        assert directory._stats["solver_success"] == 0
        assert directory._stats["solver_failures"] == 0

        _insert_events(
            db_path,
            [
                {"event_type": "clearance_refresh", "success": True},
                {"event_type": "clearance_refresh", "success": True},
                {"event_type": "clearance_refresh", "success": False},
            ],
        )
        stats = directory.get_stats()
        assert stats["stats"]["solver_success"] == 2
        assert stats["stats"]["solver_failures"] == 1
        assert stats["stats"]["total_checks"] == 3
    finally:
        os.unlink(db_path)
