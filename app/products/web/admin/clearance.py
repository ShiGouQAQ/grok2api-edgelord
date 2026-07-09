"""CF Clearance 监控接口"""

from fastapi import APIRouter, Query

from app.platform.logging.logger import logger

router = APIRouter()


@router.get("/cf-clearance/status")
async def get_cf_clearance_status():
    """获取cf_clearance状态"""
    from app.control.proxy import get_proxy_directory

    directory = await get_proxy_directory()
    return {"success": True, "data": directory.get_stats()}


@router.post("/cf-clearance/refresh")
async def refresh_cf_clearance():
    """手动刷新cf_clearance（grok.com + console.x.ai）"""
    from app.control.proxy import (
        get_proxy_directory,
        _DEFAULT_CLEARANCE_ORIGIN,
        _CONSOLE_CLEARANCE_ORIGIN,
    )

    try:
        directory = await get_proxy_directory()
        # ponytail: 刷新两个域名的 clearance，force=True 跳过缓存
        success_grok = await directory.ensure_valid_clearance(
            _DEFAULT_CLEARANCE_ORIGIN, force=True
        )
        success_console = await directory.ensure_valid_clearance(
            _CONSOLE_CLEARANCE_ORIGIN, force=True
        )
        success = success_grok or success_console
        if success:
            return {"success": True, "message": "刷新成功"}
        else:
            return {
                "success": False,
                "message": "刷新失败，请检查 Turnstile Solver 是否正常运行",
            }
    except Exception as e:
        logger.error(f"刷新 CF Clearance 失败: {e}")
        return {
            "success": False,
            "message": "刷新失败，请检查 Turnstile Solver 是否正常运行",
        }


@router.get("/cf-clearance/history")
async def get_cf_clearance_history(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=100),
    event_type: str = Query(None),
    start_time: float = Query(None),
    end_time: float = Query(None),
):
    """获取 CF Clearance 历史记录"""
    from app.control.proxy import get_proxy_directory

    try:
        directory = await get_proxy_directory()
        result = await directory.get_history(
            page=page,
            page_size=page_size,
            event_type=event_type,
            start_time=start_time,
            end_time=end_time,
        )
        return {"success": True, "data": result}
    except Exception as e:
        logger.error(f"获取 CF Clearance 历史记录失败: {e}")
        return {"success": False, "message": "获取历史记录失败，请稍后重试"}


@router.get("/cf-clearance/stats")
async def get_cf_clearance_stats():
    """获取 CF Clearance 详细统计信息"""
    from app.control.proxy import get_proxy_directory

    try:
        directory = await get_proxy_directory()
        stats = directory.get_stats()
        return {"success": True, "data": stats}
    except Exception as e:
        logger.error(f"获取 CF Clearance 统计信息失败: {e}")
        return {"success": False, "message": "获取统计信息失败，请稍后重试"}


@router.delete("/cf-clearance/history")
async def clear_cf_clearance_history():
    """清空 CF Clearance 历史记录"""
    from app.control.proxy import get_proxy_directory

    try:
        directory = await get_proxy_directory()
        await directory.clear_history()
        return {"success": True, "message": "历史记录已清空"}
    except Exception as e:
        logger.error(f"清空 CF Clearance 历史记录失败: {e}")
        return {"success": False, "message": "清空历史记录失败，请稍后重试"}
