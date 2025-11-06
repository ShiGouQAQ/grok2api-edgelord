"""CF Clearance监控接口"""

from fastapi import APIRouter
from app.services.grok.cf_clearance import cf_clearance_manager

router = APIRouter()


@router.get("/cf-clearance/status")
async def get_cf_clearance_status():
    """获取cf_clearance状态"""
    return {
        "success": True,
        "data": cf_clearance_manager.get_stats()
    }


@router.post("/cf-clearance/refresh")
async def refresh_cf_clearance():
    """手动刷新cf_clearance"""
    try:
        success = await cf_clearance_manager.ensure_valid_clearance()
        if success:
            return {
                "success": True,
                "message": "刷新成功"
            }
        else:
            return {
                "success": False,
                "message": "刷新失败，请检查 Turnstile Solver 是否正常运行"
            }
    except Exception as e:
        logger.error(f"刷新 CF Clearance 失败: {e}")
        return {
            "success": False,
            "message": f"刷新失败: {str(e)}"
        }
