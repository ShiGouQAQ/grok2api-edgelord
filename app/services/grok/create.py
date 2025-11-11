"""Post创建管理器"""

import json
from typing import Dict, Any, Optional
from curl_cffi.requests import AsyncSession

from app.services.grok.statsig import get_dynamic_headers
from app.services.grok.browser_config import CURL_IMPERSONATE
from app.core.exception import GrokApiException
from app.core.config import setting
from app.core.logger import logger

# 常量定义
CREATE_ENDPOINT = "https://grok.com/rest/media/post/create"
REQUEST_TIMEOUT = 30
IMPERSONATE_BROWSER = CURL_IMPERSONATE


class PostCreateManager:
    """
    Grok 会话创建管理器
    
    提供图片会话创建功能，用于视频生成前的准备工作
    """

    @staticmethod
    async def create(file_id: str, file_uri: str, auth_token: str) -> Optional[Dict[str, Any]]:
        """
        创建会话记录
        
        Args:
            file_id: 上传后的文件ID
            file_uri: 上传后的文件URI
            auth_token: 认证令牌
            
        Returns:
            创建的会话信息，包含会话ID等
        """
        try:
            # 验证参数
            if not file_id or not file_uri:
                raise GrokApiException("会话ID或URI缺失", "INVALID_PARAMS")
            
            if not auth_token:
                raise GrokApiException("认证令牌缺失", "NO_AUTH_TOKEN")

            # 构建创建数据
            media_url = f"https://assets.grok.com/{file_uri}"
            
            create_data = {
                "media_url": media_url,
                "media_type": "MEDIA_POST_TYPE_IMAGE"
            }

            # 获取认证令牌和cookie
            cf_cookies = setting.grok_config.get("cf_clearance", "")
            cookie = f"{auth_token}; {cf_cookies}" if cf_cookies else auth_token
            
            # 获取代理配置
            proxy_url = setting.grok_config.get("proxy_url", "")
            proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None

            # 验证代理配置
            if proxy_url:
                logger.debug(f"[PostCreate] 使用代理: {proxy_url}")
            else:
                logger.warning("[PostCreate] 未配置代理，直连请求")

            # 发送异步请求
            headers = {
                **get_dynamic_headers("/rest/media/post/create"),
                "Cookie": cookie,
            }

            logger.info(f"[PostCreate] cf_clearance: {cf_cookies[:30] if cf_cookies else 'None'}...")
            logger.info(f"[PostCreate] User-Agent: {headers.get('User-Agent', 'None')}")
            logger.info(f"[PostCreate] Sec-Ch-Ua: {headers.get('Sec-Ch-Ua', 'None')}")
            logger.info(f"[PostCreate] Sec-Ch-Ua-Platform: {headers.get('Sec-Ch-Ua-Platform', 'None')}")

            async with AsyncSession() as session:
                response = await session.post(
                    CREATE_ENDPOINT,
                    headers=headers,
                    json=create_data,
                    impersonate=IMPERSONATE_BROWSER,
                    timeout=REQUEST_TIMEOUT,
                    proxies=proxies,
                )

                # 检查响应
                if response.status_code == 200:
                    result = response.json()
                    post_id = result.get("post", {}).get("id", "")
                    logger.debug(f"[PostCreate] 创建会话成功，会话ID: {post_id}")
                    return {
                        "post_id": post_id,
                        "file_id": file_id,
                        "file_uri": file_uri,
                        "success": True,
                        "data": result
                    }
                else:
                    error_msg = f"状态码: {response.status_code}"
                    try:
                        error_data = response.json()
                        error_msg = f"{error_msg}, 详情: {error_data}"
                    except:
                        error_msg = f"{error_msg}, 详情: {response.text[:200]}"
                    
                    logger.error(f"[PostCreate] 创建会话失败: {error_msg}")
                    raise GrokApiException(f"创建会话失败: {error_msg}", "CREATE_ERROR")

        except GrokApiException:
            raise
        except Exception as e:
            error_msg = str(e)
            logger.error(f"[PostCreate] 创建会话异常: {error_msg}")
            
            # TLS错误表示代理网络问题，触发节点切换
            if "TLS connect error" in error_msg or "SSL" in error_msg or "OPENSSL" in error_msg:
                logger.warning("[PostCreate] 检测到TLS/SSL错误，可能是代理网络问题，尝试切换节点")
                try:
                    from app.services.grok.cf_clearance import cf_clearance_manager
                    switched = await cf_clearance_manager._switch_mihomo_node()
                    if switched:
                        logger.info("[PostCreate] 节点切换成功，请重试请求")
                    else:
                        logger.warning("[PostCreate] 节点切换失败或未启用Mihomo")
                except Exception as switch_err:
                    logger.error(f"[PostCreate] 节点切换异常: {switch_err}")
            
            raise GrokApiException(f"创建会话异常: {e}", "CREATE_ERROR") from e
