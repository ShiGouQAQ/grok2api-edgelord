"""Client key service — key generation, validation, scope normalization.

Port of Go ``application/clientkey/service.go`` + ``domain/clientkey`` scope
parsing.  Scopes are stored as JSON string lists; empty/``["all"]`` means
unrestricted.
"""

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.platform.runtime.clock import now_ms

from .model import (
    DEFAULT_MAX_CONCURRENT,
    DEFAULT_RPM_LIMIT,
    ClientKey,
    _KEY_PREFIX,
    _PREFIX_HEX_CHARS,
    _SECRET_HEX_CHARS,
)

# Go clientkey.Err* sentinels, mapped onto exceptions for HTTP mapping.
PROVIDERS = ("grok_build", "grok_web", "grok_console")
TIERS = ("free", "super")


class ClientKeyError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class InvalidInputError(ClientKeyError):
    pass


class NotFoundError(ClientKeyError):
    pass


class ConflictError(ClientKeyError):
    pass


@dataclass
class CreateInput:
    name: str
    enabled: bool = True
    expires_at: int | None = None  # epoch ms
    rpm_limit: int | None = None  # None → default; 0 → unlimited
    max_concurrent: int | None = None
    billing_limit_usd_ticks: int = 0
    allow_model_aliases: bool = False
    allowed_model_ids: list[str] = field(default_factory=list)
    provider_scope: list[str] = field(default_factory=list)
    tier_scope: list[str] = field(default_factory=list)


@dataclass
class CreateResult:
    key: ClientKey
    secret: str


def parse_rfc3339_ms(value: str) -> int | None:
    """Parse RFC3339 timestamp to epoch ms.  Empty → None."""
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _normalize_scope(
    values: list[str], allowed: tuple[str, ...], label: str
) -> list[str]:
    """Go Parse*ScopeValues: ``all`` alone or a combination of allowed values."""
    if not values:
        return []
    if values == ["all"]:
        return ["all"]
    if any(v not in allowed for v in values):
        raise InvalidInputError(
            "invalidAccountScope", f"{label} 必须是 all，或 {', '.join(allowed)} 的组合"
        )
    return sorted(set(values))


def resolve_scopes(
    provider_scope: list[str],
    tier_scope: list[str],
    account_pool: str | None,
) -> tuple[list[str], list[str]]:
    """Port of Go ``parseRequestedScopes`` (legacy accountPool → scopes)."""
    if account_pool is not None and (provider_scope or tier_scope):
        raise InvalidInputError(
            "invalidAccountScope",
            "accountPool 不能与 providerScope 或 tierScope 同时设置",
        )
    if account_pool is not None:
        if account_pool.strip() not in ("all", "free", "super"):
            raise InvalidInputError(
                "invalidAccountScope", "accountPool 必须是 all、free 或 super"
            )
        return ["all"], [account_pool.strip()]
    return (
        _normalize_scope(provider_scope, PROVIDERS, "providerScope"),
        _normalize_scope(tier_scope, TIERS, "tierScope"),
    )


def _validate_model_ids(ids: list[str]) -> list[str]:
    for value in ids:
        if not str(value).strip():
            raise InvalidInputError("invalidModelId", "allowedModelIds 包含无效 ID")
    return list(ids)


def generate_secret() -> str:
    """Client key secret: ``grok2api_`` + 32 hex chars (Go sk-style prefix)."""
    return f"{_KEY_PREFIX}{secrets.token_hex(_SECRET_HEX_CHARS // 2)}"


class ClientKeyService:
    def __init__(self, repository) -> None:
        self._repo = repository

    async def create(self, input_data: CreateInput) -> CreateResult:
        name = str(input_data.name or "").strip()
        if not name:
            raise InvalidInputError("invalidInput", "name 不能为空")
        if len(name) > 64:
            raise InvalidInputError("invalidInput", "name 过长（最多 64 字符）")

        secret = generate_secret()
        expires_at = input_data.expires_at
        if expires_at is not None and expires_at <= now_ms():
            raise InvalidInputError("invalidExpiresAt", "expiresAt 必须是未来时间")

        rpm_limit = (
            input_data.rpm_limit
            if input_data.rpm_limit is not None
            else DEFAULT_RPM_LIMIT
        )
        max_concurrent = (
            input_data.max_concurrent
            if input_data.max_concurrent is not None
            else DEFAULT_MAX_CONCURRENT
        )
        if rpm_limit < 0 or rpm_limit > 100_000:  # Go MaxRPMLimit
            raise InvalidInputError("invalidInput", "rpmLimit 必须在 0~100000 之间")
        if max_concurrent < 0 or max_concurrent > 1024:  # Go MaxConcurrent
            raise InvalidInputError("invalidInput", "maxConcurrent 必须在 0~1024 之间")
        if input_data.billing_limit_usd_ticks < 0:
            raise InvalidInputError("invalidInput", "billingLimitUsdTicks 不能为负")

        provider_scope, tier_scope = resolve_scopes(
            input_data.provider_scope, input_data.tier_scope, None
        )

        key = ClientKey(
            name=name,
            prefix=secret[: len(_KEY_PREFIX) + _PREFIX_HEX_CHARS],
            secret=secret,
            enabled=input_data.enabled,
            expires_at=expires_at,
            rpm_limit=rpm_limit,
            max_concurrent=max_concurrent,
            billing_limit_usd_ticks=input_data.billing_limit_usd_ticks,
            allow_model_aliases=input_data.allow_model_aliases,
            allowed_model_ids=_validate_model_ids(input_data.allowed_model_ids),
            provider_scope=provider_scope,
            tier_scope=tier_scope,
        )
        created = await self._repo.create(key)
        return CreateResult(key=created, secret=secret)

    async def get(self, key_id: int) -> ClientKey:
        key = await self._repo.get(key_id)
        if key is None:
            raise NotFoundError("clientKeyNotFound", "客户端 Key 不存在")
        return key

    async def update(
        self,
        key_id: int,
        *,
        name: str | None = None,
        enabled: bool | None = None,
        expires_at: int | None = None,
        clear_expires_at: bool = False,
        rpm_limit: int | None = None,
        max_concurrent: int | None = None,
        billing_limit_usd_ticks: int | None = None,
        allow_model_aliases: bool | None = None,
        allowed_model_ids: list[str] | None = None,
        provider_scope: list[str] | None = None,
        tier_scope: list[str] | None = None,
    ) -> ClientKey:
        key = await self.get(key_id)

        if name is not None:
            name = str(name).strip()
            if not name:
                raise InvalidInputError("invalidInput", "name 不能为空")
            key.name = name
        if enabled is not None:
            key.enabled = enabled
        if clear_expires_at:
            key.expires_at = None
        elif expires_at is not None:
            key.expires_at = expires_at
        if rpm_limit is not None:
            if rpm_limit < 0 or rpm_limit > 100_000:
                raise InvalidInputError("invalidInput", "rpmLimit 必须在 0~100000 之间")
            key.rpm_limit = rpm_limit
        if max_concurrent is not None:
            if max_concurrent < 0 or max_concurrent > 1024:
                raise InvalidInputError(
                    "invalidInput", "maxConcurrent 必须在 0~1024 之间"
                )
            key.max_concurrent = max_concurrent
        if billing_limit_usd_ticks is not None:
            if billing_limit_usd_ticks < 0:
                raise InvalidInputError("invalidInput", "billingLimitUsdTicks 不能为负")
            key.billing_limit_usd_ticks = billing_limit_usd_ticks
        if allow_model_aliases is not None:
            key.allow_model_aliases = allow_model_aliases
        if allowed_model_ids is not None:
            key.allowed_model_ids = _validate_model_ids(allowed_model_ids)
        if provider_scope is not None or tier_scope is not None:
            providers, tiers = resolve_scopes(
                key.provider_scope if provider_scope is None else provider_scope,
                key.tier_scope if tier_scope is None else tier_scope,
                None,
            )
            key.provider_scope, key.tier_scope = providers, tiers

        return await self._repo.update(key)

    async def delete(self, key_id: int) -> None:
        if not await self._repo.delete(key_id):
            raise NotFoundError("clientKeyNotFound", "客户端 Key 不存在")


__all__ = [
    "ClientKeyService",
    "ClientKeyError",
    "InvalidInputError",
    "NotFoundError",
    "ConflictError",
    "CreateInput",
    "CreateResult",
    "parse_rfc3339_ms",
    "resolve_scopes",
    "generate_secret",
]
