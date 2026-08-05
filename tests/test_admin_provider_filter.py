"""Provider-scoped "all accounts" batch operations (Web vs Build).

Frontend passes `?provider=grok_web|grok_build` so global batch actions on the
Build tab operate only on build accounts, and vice versa.
"""

import asyncio
import unittest

import orjson

from app.control.account.enums import AccountStatus
from app.control.account.models import AccountRecord
from app.control.account.refresh import RefreshResult
from app.platform.errors import ValidationError
from app.products.web.admin.batch import (
    BatchRequest,
    _list_all_tokens,
    batch_nsfw,
    batch_refresh,
)
from app.products.web.admin import tokens as admin_tokens


def _record(
    token: str,
    *,
    provider: str = "grok_web",
    status: AccountStatus = AccountStatus.ACTIVE,
) -> AccountRecord:
    return AccountRecord(
        token=token,
        status=status,
        provider=provider,
    )


class _Page:
    def __init__(self, items: list) -> None:
        self.items = items
        self.total_pages = 1
        self.page = 1


class _Repo:
    def __init__(self) -> None:
        self.records = {
            "web-token": _record("web-token"),
            "build-token": _record("build-token", provider="grok_build"),
            "disabled-token": _record(
                "disabled-token", provider="grok_build", status=AccountStatus.DISABLED
            ),
            "expired-web": _record("expired-web", status=AccountStatus.EXPIRED),
        }
        self.list_accounts_calls: list[object] = []
        self.list_invalid_calls = 0
        self.deleted: list[str] = []

    async def list_accounts(self, query) -> _Page:
        self.list_accounts_calls.append(query)
        return _Page(list(self.records.values()))

    async def get_accounts(self, tokens: list[str]) -> list[AccountRecord]:
        return [self.records[t] for t in tokens if t in self.records]

    async def list_invalid_tokens(self) -> list[str]:
        self.list_invalid_calls += 1
        return ["expired-web"]

    async def delete_accounts(self, tokens: list[str]) -> None:
        self.deleted.extend(tokens)


class _RefreshService:
    def __init__(self) -> None:
        self.refreshed_tokens: list[str] = []

    async def refresh_tokens(self, tokens: list[str]) -> RefreshResult:
        self.refreshed_tokens.extend(tokens)
        return RefreshResult(refreshed=len(tokens))


class _NsfwOne:
    def __init__(self) -> None:
        self.tokens: list[str] = []

    async def __call__(self, repo, token: str, enabled: bool) -> dict:
        self.tokens.append(token)
        return {"token": token}


class ProviderBatchTests(unittest.IsolatedAsyncioTestCase):
    def _repo(self) -> _Repo:
        return _Repo()

    async def test_list_all_tokens_filters_by_provider(self):
        repo = self._repo()

        tokens = await _list_all_tokens(repo, provider="grok_build")

        self.assertEqual(tokens, ["build-token"])

    async def test_batch_refresh_all_manageable_build_only(self):
        repo = self._repo()
        refresh_svc = _RefreshService()

        await batch_refresh(
            BatchRequest(tokens=[]),
            async_mode=False,
            all_manageable=True,
            concurrency=None,
            repo=repo,
            refresh_svc=refresh_svc,
            provider="grok_build",
        )

        self.assertEqual(refresh_svc.refreshed_tokens, ["build-token"])

    async def test_batch_refresh_all_manageable_web_only(self):
        repo = self._repo()
        refresh_svc = _RefreshService()

        await batch_refresh(
            BatchRequest(tokens=[]),
            async_mode=False,
            all_manageable=True,
            concurrency=None,
            repo=repo,
            refresh_svc=refresh_svc,
            provider="grok_web",
        )

        self.assertEqual(refresh_svc.refreshed_tokens, ["web-token"])

    async def test_batch_nsfw_all_manageable_web_only(self):
        repo = self._repo()
        nsfw = _NsfwOne()

        # Patch module-level _nsfw_one
        import app.products.web.admin.batch as batch_mod

        saved = batch_mod._nsfw_one
        batch_mod._nsfw_one = nsfw
        try:
            await batch_nsfw(
                BatchRequest(tokens=[]),
                async_mode=False,
                all_manageable=True,
                concurrency=None,
                repo=repo,
                provider="grok_web",
            )
        finally:
            batch_mod._nsfw_one = saved

        self.assertEqual(nsfw.tokens, ["web-token"])

    async def test_batch_rejects_invalid_provider(self):
        repo = self._repo()
        refresh_svc = _RefreshService()

        with self.assertRaises(ValidationError):
            await batch_refresh(
                BatchRequest(tokens=[]),
                async_mode=False,
                all_manageable=True,
                concurrency=None,
                repo=repo,
                refresh_svc=refresh_svc,
                provider="bogus",
            )

    async def test_delete_invalid_tokens_provider_scoped(self):
        repo = self._repo()

        await admin_tokens.delete_invalid_tokens(repo=repo, provider="grok_build")

        # Fast path bypassed when provider given: list_invalid_tokens not called,
        # only build invalid accounts deleted (none here) — web invalid kept.
        self.assertEqual(repo.list_invalid_calls, 0)
        self.assertEqual(repo.deleted, [])

    async def test_delete_invalid_tokens_web_provider_scoped(self):
        repo = self._repo()

        await admin_tokens.delete_invalid_tokens(repo=repo, provider="grok_web")

        self.assertEqual(repo.list_invalid_calls, 0)
        self.assertEqual(repo.deleted, ["expired-web"])


if __name__ == "__main__":
    unittest.main()
