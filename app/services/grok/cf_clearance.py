"""CF Clearance自动化管理模块"""

import asyncio
import aiohttp
import time
from typing import Optional, Dict, Any

from app.core.logger import logger
from app.core.config import setting


class CFClearanceManager:
    """CF Clearance管理器"""

    _instance: Optional['CFClearanceManager'] = None
    _lock = asyncio.Lock()

    def __new__(cls) -> 'CFClearanceManager':
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, '_initialized'):
            return
        self._initialized = True
        self.last_check_time: float = 0
        self.check_interval: int = 3600
        self.stats = {
            "total_checks": 0,
            "cache_hits": 0,
            "cache_misses": 0,
            "solver_success": 0,
            "solver_failures": 0
        }
        self.mihomo_initialized: bool = False
        self.node_blacklist: set = set()
        self.last_node_list: list = []
        
        if setting.grok_config.get("mihomo_enabled", False):
            asyncio.create_task(self._init_mihomo())

    async def _init_mihomo(self) -> None:
        """初始化mihomo并选择最优节点"""
        try:
            mihomo_api = setting.grok_config.get("mihomo_api_url", "http://127.0.0.1:9091")
            group_name = setting.grok_config.get("mihomo_group_name", "XAI-GROUP")

            async with aiohttp.ClientSession() as session:
                async with session.get(f"{mihomo_api}/proxies/{group_name}", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status != 200:
                        logger.warning(f"[Mihomo] 初始化失败: {resp.status}")
                        return

                    group_data = await resp.json()
                    all_nodes = group_data.get("all", [])
                    self.last_node_list = [n for n in all_nodes if n != "DIRECT"]

                    providers_data = await self._get_providers_info(session, mihomo_api)
                    best_node = self._select_best_node_from_providers(self.last_node_list, providers_data)

                    if best_node and best_node != group_data.get("now"):
                        async with session.put(f"{mihomo_api}/proxies/{group_name}",
                                             json={"name": best_node},
                                             timeout=aiohttp.ClientTimeout(total=5)) as resp:
                            if resp.status == 204:
                                logger.info(f"[Mihomo] 已初始化并切换到最优节点: {best_node}")
                                self.mihomo_initialized = True
                            else:
                                logger.warning(f"[Mihomo] 切换节点失败: {resp.status}")
                    else:
                        self.mihomo_initialized = True
                        logger.info(f"[Mihomo] 已初始化，当前节点: {group_data.get('now')}")

        except Exception as e:
            logger.error(f"[Mihomo] 初始化异常: {e}")

    async def _get_providers_info(self, session: aiohttp.ClientSession, mihomo_api: str) -> Dict[str, Any]:
        """获取所有providers信息"""
        try:
            async with session.get(f"{mihomo_api}/providers/proxies", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    return data.get("providers", {})
        except Exception as e:
            logger.debug(f"[Mihomo] 获取providers信息失败: {e}")
        return {}

    def _select_best_node_from_providers(self, available_nodes: list, providers_data: Dict[str, Any]) -> Optional[str]:
        """从providers数据中选择最优节点"""
        if not available_nodes:
            return None
        
        # 过滤黑名单节点
        candidate_nodes = [n for n in available_nodes if n not in self.node_blacklist]
        if not candidate_nodes:
            return None
        
        # 收集所有节点的延迟信息
        node_delays = {}
        for provider_name, provider_info in providers_data.items():
            proxies = provider_info.get("proxies", [])
            for proxy in proxies:
                node_name = proxy.get("name")
                if node_name not in candidate_nodes:
                    continue
                
                history = proxy.get("history", [])
                if history:
                    latest = history[-1]
                    delay = latest.get("delay", 9999)
                    if delay > 0:
                        node_delays[node_name] = delay
        
        # 选择延迟最低的节点
        if node_delays:
            best_node = min(node_delays.items(), key=lambda x: x[1])[0]
            return best_node
        
        # 没有延迟数据，返回第一个可用节点
        return candidate_nodes[0] if candidate_nodes else None
    

    async def ensure_valid_clearance(self, force: bool = False) -> bool:
        """确保cf_clearance有效

        Args:
            force: 强制刷新，忽略缓存和启用状态检查（用于403错误时）
        """
        if not force and not self._is_enabled():
            return True

        self.stats["total_checks"] += 1

        # 检查缓存（除非强制刷新）
        if not force and self._is_cache_valid():
            self.stats["cache_hits"] += 1
            return True

        self.stats["cache_misses"] += 1

        async with self._lock:
            # 双重检查（除非强制刷新）
            if not force and self._is_cache_valid():
                return True

            # 获取新的cf_clearance
            success = await self._refresh_clearance()
            if success:
                self.stats["solver_success"] += 1
                self.last_check_time = time.time()
            else:
                self.stats["solver_failures"] += 1

            return success

    def _is_enabled(self) -> bool:
        """检查功能是否启用"""
        return setting.grok_config.get("cf_clearance_enabled", False)

    def _is_cache_valid(self) -> bool:
        """检查缓存是否有效"""
        if not self.last_check_time:
            return False
        elapsed = time.time() - self.last_check_time
        return elapsed < self.check_interval

    async def _refresh_clearance(self) -> bool:
        """刷新cf_clearance"""
        # 先检测是否有CF拦截
        if not await self._check_cf_challenge():
            logger.info("[CFClearance] 未检测到CF拦截，跳过求解")
            return True

        if not setting.grok_config.get("mihomo_enabled", False):
            return await self._try_refresh_once()

        # mihomo启用时，带重试逻辑
        max_retries = 3
        for attempt in range(max_retries):
            current_node = await self._get_current_node()
            logger.info(f"[Mihomo] 尝试求解 (第 {attempt + 1}/{max_retries} 次，当前节点: {current_node})")
            success = await self._try_refresh_once()

            if success:
                return True

            # 失败，加入黑名单
            if current_node:
                self.node_blacklist.add(current_node)
                logger.warning(f"[Mihomo] 节点 {current_node} 已加入黑名单")

            # 检查节点列表是否变化（元素或数量变化）
            new_node_list = await self._get_node_list()
            if new_node_list and (len(new_node_list) != len(self.last_node_list) or
                                  set(new_node_list) != set(self.last_node_list)):
                logger.info("[Mihomo] 检测到节点列表变化，清空黑名单")
                self.node_blacklist.clear()
                self.last_node_list = new_node_list

            # 尝试切换节点
            switched = await self._switch_mihomo_node()
            if not switched:
                logger.error("[Mihomo] 所有节点已耗尽")
                return False

        logger.error(f"[Mihomo] 达到最大重试次数 ({max_retries})，求解失败")
        return False

    async def _check_cf_challenge(self) -> bool:
        """检测是否存在CF拦截"""
        try:
            proxy_url = setting.grok_config.get("proxy_url", "")
            timeout = aiohttp.ClientTimeout(total=10)

            async with aiohttp.ClientSession(timeout=timeout) as session:
                kwargs = {}
                if proxy_url:
                    kwargs["proxy"] = proxy_url

                async with session.get("https://grok.com", **kwargs) as resp:
                    if resp.status == 403:
                        return True
                    text = await resp.text()
                    return "challenge-platform" in text or "cf-challenge" in text
        except Exception as e:
            logger.debug(f"[CFClearance] CF检测异常: {e}")
            return True
    
    async def _try_refresh_once(self) -> bool:
        """单次刷新尝试"""
        try:
            from app.services.turnstile.manager import turnstile_manager
            
            target_url = "https://grok.com"
            
            logger.info(f"[CFClearance] 使用playwright-captcha求解验证码")
            
            clearance = await turnstile_manager.solve_cloudflare(url=target_url)
            
            if clearance:
                await setting.save(grok_config={"cf_clearance": clearance})
                logger.info(f"[CFClearance] 已更新cf_clearance")
                return True
            
            logger.error(f"[CFClearance] 求解失败")
            return False

        except Exception as e:
            logger.error(f"[CFClearance] 刷新失败: {e}")
            return False


    async def _switch_mihomo_node(self) -> bool:
        """切换mihomo代理节点到最优节点"""
        if not setting.grok_config.get("mihomo_enabled", False):
            return False

        try:
            mihomo_api = setting.grok_config.get("mihomo_api_url", "http://127.0.0.1:9091")
            group_name = setting.grok_config.get("mihomo_group_name", "XAI-GROUP")

            async with aiohttp.ClientSession() as session:
                async with session.get(f"{mihomo_api}/proxies/{group_name}", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status != 200:
                        logger.error(f"[Mihomo] 获取节点组失败: {resp.status}")
                        return False

                    group_data = await resp.json()
                    current = group_data.get("now")
                    all_nodes = [n for n in group_data.get("all", []) if n != "DIRECT"]

                    providers_data = await self._get_providers_info(session, mihomo_api)
                    best_node = self._select_best_node_from_providers(all_nodes, providers_data)

                    if not best_node:
                        logger.error("[Mihomo] 没有可用节点")
                        return False

                    if best_node == current:
                        logger.info(f"[Mihomo] 当前已是最优节点: {best_node}")
                        return True

                    async with session.put(f"{mihomo_api}/proxies/{group_name}",
                                         json={"name": best_node},
                                         timeout=aiohttp.ClientTimeout(total=5)) as resp:
                        if resp.status == 204:
                            logger.info(f"[Mihomo] 已切换节点: {current} -> {best_node}")
                            return True
                        else:
                            logger.error(f"[Mihomo] 切换节点失败: {resp.status}")
                            return False

        except Exception as e:
            logger.error(f"[Mihomo] 切换节点异常: {e}")
            return False

    async def _get_current_node(self) -> Optional[str]:
        """获取当前使用的节点"""
        if not setting.grok_config.get("mihomo_enabled", False):
            return None

        try:
            mihomo_api = setting.grok_config.get("mihomo_api_url", "http://127.0.0.1:9091")
            group_name = setting.grok_config.get("mihomo_group_name", "XAI-GROUP")

            async with aiohttp.ClientSession() as session:
                async with session.get(f"{mihomo_api}/proxies/{group_name}", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        group_data = await resp.json()
                        return group_data.get("now")
        except Exception:
            pass
        return None
    
    async def _get_node_list(self) -> list:
        """获取当前节点列表"""
        if not setting.grok_config.get("mihomo_enabled", False):
            return []

        try:
            mihomo_api = setting.grok_config.get("mihomo_api_url", "http://127.0.0.1:9091")
            group_name = setting.grok_config.get("mihomo_group_name", "XAI-GROUP")

            async with aiohttp.ClientSession() as session:
                async with session.get(f"{mihomo_api}/proxies/{group_name}", timeout=aiohttp.ClientTimeout(total=5)) as resp:
                    if resp.status == 200:
                        group_data = await resp.json()
                        return [n for n in group_data.get("all", []) if n != "DIRECT"]
        except Exception:
            pass
        return []

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        total = self.stats["total_checks"]
        return {
            "enabled": self._is_enabled(),
            "cache_valid": self._is_cache_valid(),
            "stats": self.stats.copy(),
            "hit_rate": self.stats["cache_hits"] / max(total, 1)
        }


# 全局实例
cf_clearance_manager = CFClearanceManager()
