"""Mihomo proxy node management.

Provides Mihomo REST API integration for proxy group switching,
latency-based optimal node selection, and node blacklisting.
"""

from __future__ import annotations

from typing import Any

import aiohttp

from app.platform.config.snapshot import get_config
from app.platform.logging.logger import logger


class MihomoClient:
    """Mihomo proxy node manager.

    Features:
    - Mihomo REST API integration (get groups, switch nodes)
    - Latency-based optimal node selection (from providers historical data)
    - Node blacklist mechanism (auto-exclude failed nodes, clear on node list change)
    """

    def __init__(self) -> None:
        self._blacklist: set[str] = set()
        self._last_node_set: frozenset[str] = frozenset()

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _enabled() -> bool:
        """Check if Mihomo mode is active."""
        from app.control.proxy.models import EgressMode

        mode = get_config("proxy.egress.mode", "direct")
        return EgressMode(mode) == EgressMode.MIHOMO

    @staticmethod
    def _api_url() -> str:
        return str(get_config("proxy.egress.mihomo_api_url", "http://127.0.0.1:9093"))

    @staticmethod
    def _group_name() -> str:
        return str(get_config("proxy.egress.mihomo_group_name", "XAI-GROUP"))

    # ------------------------------------------------------------------
    # Blacklist management
    # ------------------------------------------------------------------

    def add_blacklist(self, name: str) -> None:
        """Add a node to the blacklist."""
        self._blacklist.add(name)
        logger.debug("mihomo blacklist add: node={}", name)

    def clear_blacklist(self) -> None:
        """Clear the entire blacklist."""
        self._blacklist.clear()
        logger.debug("mihomo blacklist cleared")

    def _update_blacklist_on_node_change(self, current_nodes: set[str]) -> None:
        """Clear blacklist if the node set has changed."""
        current = frozenset(current_nodes)
        if not self._last_node_set:
            # First call - initialize without clearing blacklist
            self._last_node_set = current
            return
        if current != self._last_node_set:
            logger.info(
                "mihomo node list changed: old_count={} new_count={}",
                len(self._last_node_set),
                len(current),
            )
            self._last_node_set = current
            self.clear_blacklist()

    # ------------------------------------------------------------------
    # Mihomo REST API
    # ------------------------------------------------------------------

    async def get_group_nodes(self) -> dict[str, Any] | None:
        """Fetch proxy group info from Mihomo REST API.

        Returns the full group dict (with ``all`` key containing node list),
        or ``None`` on failure.
        """
        if not self._enabled():
            return None

        url = f"{self._api_url().rstrip('/')}/proxies/{self._group_name()}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status == 200:
                        return await resp.json()
                    logger.warning(
                        "mihomo get group failed: url={} status={}", url, resp.status
                    )
                    return None
        except Exception as exc:
            logger.warning("mihomo get group failed: url={} error={}", url, exc)
            return None

    async def get_current_node(self) -> str | None:
        """Get the currently active node name."""
        group = await self.get_group_nodes()
        if group:
            return group.get("now")
        return None

    async def switch_node(self, node_name: str) -> bool:
        """Switch the active node of the proxy group.

        Returns ``True`` on success, ``False`` on failure.
        """
        if not self._enabled():
            return False

        url = f"{self._api_url().rstrip('/')}/proxies/{self._group_name()}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.put(
                    url,
                    json={"name": node_name},
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status == 204:
                        logger.info(
                            "mihomo switched node: group={} node={}",
                            self._group_name(),
                            node_name,
                        )
                        return True
                    logger.warning(
                        "mihomo switch node failed: node={} status={}",
                        node_name,
                        resp.status,
                    )
                    return False
        except Exception as exc:
            logger.warning(
                "mihomo switch node failed: node={} error={}", node_name, exc
            )
            return False

    # ------------------------------------------------------------------
    # Optimal node selection
    # ------------------------------------------------------------------

    async def select_optimal_node(self, exclude_current: bool = False) -> str | None:
        """Select the optimal node based on historical latency data.

        Args:
            exclude_current: If True, exclude the currently active node from selection.

        Returns the node name, or ``None`` if no suitable node is found.
        """
        group = await self.get_group_nodes()
        if not group:
            return None

        all_nodes: list[str] = group.get("all", [])
        if not all_nodes:
            return None

        self._update_blacklist_on_node_change(set(all_nodes))

        available = [n for n in all_nodes if n not in self._blacklist]

        # Optionally exclude the current node
        if exclude_current:
            current_node = group.get("now")
            if current_node and current_node in available:
                available = [n for n in available if n != current_node]

        if not available:
            logger.warning("mihomo all nodes blacklisted, clearing blacklist")
            self.clear_blacklist()
            available = all_nodes
            if exclude_current:
                current_node = group.get("now")
                if current_node and len(available) > 1:
                    available = [n for n in available if n != current_node]

        # Get latency data from providers
        providers = group.get("providers", {})
        latency_map: dict[str, float] = {}

        for provider_data in providers.values():
            for node in provider_data.get("nodes", []):
                name = node.get("name", "")
                history = node.get("history", [])
                if history and name in available:
                    latency = history[-1].get("delay", 99999)
                    latency_map[name] = float(latency)

        if not latency_map:
            logger.debug("mihomo no latency data, returning first available node")
            return available[0] if available else None

        best_node = min(latency_map.items(), key=lambda x: x[1])[0]
        logger.debug(
            "mihomo optimal node selected: node={} latency={}ms candidates={}",
            best_node,
            latency_map[best_node],
            len(latency_map),
        )
        return best_node

    async def switch_to_optimal(self, exclude_current: bool = False) -> bool:
        """Select and switch to the optimal node.

        Args:
            exclude_current: If True, exclude the currently active node.

        Returns ``True`` on success, ``False`` on failure.
        """
        if not self._enabled():
            return False

        node = await self.select_optimal_node(exclude_current=exclude_current)
        if not node:
            logger.warning("mihomo no optimal node found")
            return False

        return await self.switch_node(node)

    async def switch_and_blacklist_current(self) -> bool:
        """Switch to a different node and blacklist the current one.

        This is used when the current node is banned by Cloudflare.
        The current node is added to blacklist before switching.

        Returns ``True`` on success, ``False`` on failure.
        """
        if not self._enabled():
            return False

        # Get current node before switching
        current_node = await self.get_current_node()
        if current_node:
            self.add_blacklist(current_node)
            logger.info("mihomo blacklisted current node: {}", current_node)

        # Switch to a different node (excluding current)
        return await self.switch_to_optimal(exclude_current=True)


__all__ = ["MihomoClient"]
