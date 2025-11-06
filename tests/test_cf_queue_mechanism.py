"""CF求解队列机制测试"""

import asyncio
import pytest
from unittest.mock import AsyncMock, patch
from app.services.grok.token import GrokTokenManager


@pytest.fixture
def token_manager():
    """创建测试token管理器实例"""
    mgr = GrokTokenManager.__new__(GrokTokenManager)
    mgr._cf_solving_lock = asyncio.Lock()
    mgr._cf_solving_event = asyncio.Event()
    mgr._cf_solving_event.set()
    return mgr


@pytest.mark.asyncio
async def test_first_403_blocks_and_solves(token_manager):
    """测试第一个403请求阻塞并求解"""
    with patch('app.services.grok.cf_clearance.cf_clearance_manager') as mock_cf:
        mock_cf.ensure_valid_clearance = AsyncMock(return_value=True)

        await token_manager._auto_solve_cf_clearance()

        mock_cf.ensure_valid_clearance.assert_called_once_with(force=True)


@pytest.mark.asyncio
async def test_concurrent_403_waits_for_first(token_manager):
    """测试并发403请求等待第一个完成"""
    solve_count = 0

    async def mock_solve(force=False):
        nonlocal solve_count
        solve_count += 1
        await asyncio.sleep(0.1)  # 模拟求解耗时
        return True

    with patch('app.services.grok.cf_clearance.cf_clearance_manager') as mock_cf:
        mock_cf.ensure_valid_clearance = mock_solve

        # 并发3个求解请求
        tasks = [
            token_manager._auto_solve_cf_clearance(),
            token_manager._auto_solve_cf_clearance(),
            token_manager._auto_solve_cf_clearance()
        ]
        await asyncio.gather(*tasks)

        # 只应该求解一次
        assert solve_count == 1


@pytest.mark.asyncio
async def test_event_notifies_waiting_requests(token_manager):
    """测试Event正确通知等待的请求"""
    started = []
    completed = []

    async def mock_solve(force=False):
        await asyncio.sleep(0.05)
        return True

    with patch('app.services.grok.cf_clearance.cf_clearance_manager') as mock_cf:
        mock_cf.ensure_valid_clearance = mock_solve

        async def request(idx):
            started.append(idx)
            await token_manager._auto_solve_cf_clearance()
            completed.append(idx)

        # 启动5个并发请求
        await asyncio.gather(*[request(i) for i in range(5)])

        # 所有请求都应该完成
        assert len(started) == 5
        assert len(completed) == 5


@pytest.mark.asyncio
async def test_lock_released_on_exception(token_manager):
    """测试异常时锁被正确释放"""
    with patch('app.services.grok.cf_clearance.cf_clearance_manager') as mock_cf:
        mock_cf.ensure_valid_clearance = AsyncMock(side_effect=Exception("Solve failed"))

        # 第一次求解失败
        await token_manager._auto_solve_cf_clearance()

        # 锁应该被释放，可以再次求解
        assert not token_manager._cf_solving_lock.locked()

        # 第二次求解应该能正常进行
        mock_cf.ensure_valid_clearance = AsyncMock(return_value=True)
        await token_manager._auto_solve_cf_clearance()

        mock_cf.ensure_valid_clearance.assert_called_once_with(force=True)


@pytest.mark.asyncio
async def test_event_set_after_solve_completes(token_manager):
    """测试求解完成后Event被设置"""
    with patch('app.services.grok.cf_clearance.cf_clearance_manager') as mock_cf:
        mock_cf.ensure_valid_clearance = AsyncMock(return_value=True)

        # 清除Event
        token_manager._cf_solving_event.clear()

        await token_manager._auto_solve_cf_clearance()

        # Event应该被设置
        assert token_manager._cf_solving_event.is_set()


@pytest.mark.asyncio
async def test_subsequent_requests_after_solve(token_manager):
    """测试求解完成后的后续请求"""
    solve_count = 0

    async def mock_solve(force=False):
        nonlocal solve_count
        solve_count += 1
        await asyncio.sleep(0.05)
        return True

    with patch('app.services.grok.cf_clearance.cf_clearance_manager') as mock_cf:
        mock_cf.ensure_valid_clearance = mock_solve

        # 第一次求解
        await token_manager._auto_solve_cf_clearance()
        assert solve_count == 1

        # 第二次求解（新的403）
        await token_manager._auto_solve_cf_clearance()
        assert solve_count == 2


@pytest.mark.asyncio
async def test_no_blocking_for_non_403_requests(token_manager):
    """测试非403请求不受影响"""
    # 模拟正在进行的CF求解
    async def long_solve(force=False):
        await asyncio.sleep(1)
        return True

    with patch('app.services.grok.cf_clearance.cf_clearance_manager') as mock_cf:
        mock_cf.ensure_valid_clearance = long_solve

        # 启动一个长时间的求解任务
        solve_task = asyncio.create_task(token_manager._auto_solve_cf_clearance())

        # 等待锁被获取
        await asyncio.sleep(0.01)

        # 其他操作不应该被阻塞（这里只是验证不会死锁）
        assert token_manager._cf_solving_lock.locked()

        # 取消求解任务
        solve_task.cancel()
        try:
            await solve_task
        except asyncio.CancelledError:
            pass
