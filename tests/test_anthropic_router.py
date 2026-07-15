"""_safe_sse_anthropic error passthrough unit tests.

Tests the SSE error wrapper that converts exceptions to Anthropic-format
error events, with AppError.kind / UpstreamError.upstream_code passthrough
(ported from Go commit 982e27b).
"""

import pytest
import orjson
from app.platform.errors import AppError, UpstreamError, ValidationError, ErrorKind
from app.products.anthropic.router import _safe_sse_anthropic


async def _stream_from_list(items: list) -> str:
    """Collect all chunks from _safe_sse_anthropic over a list-based stream."""

    async def _gen():
        for item in items:
            yield item

    chunks: list[str] = []
    async for chunk in _safe_sse_anthropic(_gen()):
        chunks.append(chunk)
    return "".join(chunks)


async def _stream_that_raises(exc: Exception) -> str:
    """Collect chunks from a stream that raises immediately."""

    async def _gen():
        raise exc
        yield  # pragma: no cover

    chunks: list[str] = []
    async for chunk in _safe_sse_anthropic(_gen()):
        chunks.append(chunk)
    return "".join(chunks)


class TestSafeSseAnthropicNormal:
    """Normal (non-error) stream passthrough."""

    @pytest.mark.asyncio
    async def test_passthrough_normal_chunks(self):
        """Given a normal stream, chunks pass through unchanged."""
        chunks = ["data: hello\n\n", "data: world\n\n"]
        result = await _stream_from_list(chunks)
        assert result == "data: hello\n\ndata: world\n\n"

    @pytest.mark.asyncio
    async def test_empty_stream(self):
        """Given an empty stream, nothing is yielded."""
        result = await _stream_from_list([])
        assert result == ""


class TestSafeSseAnthropicAppError:
    """AppError passthrough — error.kind used as inner type."""

    @pytest.mark.asyncio
    async def test_app_error_uses_kind(self):
        """AppError (non-UpstreamError) → inner error type is exc.kind."""
        exc = ValidationError("invalid model", param="model")
        result = await _stream_that_raises(exc)
        assert "event: error" in result
        assert "data: [DONE]" in result
        # Parse the SSE data line
        for line in result.split("\n"):
            if line.startswith("data: ") and "[DONE]" not in line:
                payload = orjson.loads(line[6:])
                assert payload["type"] == "error"
                assert (
                    payload["error"]["type"] == "invalid_request_error"
                )  # ErrorKind.VALIDATION
                assert payload["error"]["code"] == "invalid_value"
                assert payload["error"]["message"] == "invalid model"
                assert payload["error"]["param"] == "model"

    @pytest.mark.asyncio
    async def test_validation_error_keeps_param(self):
        """ValidationError param is preserved in error dict."""
        exc = ValidationError("bad request", param="model", code="model_not_found")
        result = await _stream_that_raises(exc)
        for line in result.split("\n"):
            if line.startswith("data: ") and "[DONE]" not in line:
                payload = orjson.loads(line[6:])
                assert payload["error"]["param"] == "model"


class TestSafeSseAnthropicUpstreamError:
    """UpstreamError — upstream_code overrides error type."""

    @pytest.mark.asyncio
    async def test_upstream_error_with_code(self):
        """UpstreamError with upstream_code → inner type is upstream_code (+ body)."""
        exc = UpstreamError(
            "access denied",
            status=403,
            body='{"error":"access denied"}',
            upstream_code="access_denied",
            credential_rejected=True,
        )
        result = await _stream_that_raises(exc)
        for line in result.split("\n"):
            if line.startswith("data: ") and "[DONE]" not in line:
                payload = orjson.loads(line[6:])
                assert payload["type"] == "error"
                # upstream_code overrides the error type — from Go commit 982e27b
                assert payload["error"]["type"] == "access_denied"
                assert payload["error"]["body"] == '{"error":"access denied"}'

    @pytest.mark.asyncio
    async def test_upstream_error_without_code(self):
        """UpstreamError without upstream_code → inner type is kind."""
        exc = UpstreamError("quota exceeded", status=429, body="rate limited")
        result = await _stream_that_raises(exc)
        for line in result.split("\n"):
            if line.startswith("data: ") and "[DONE]" not in line:
                payload = orjson.loads(line[6:])
                assert payload["type"] == "error"
                assert payload["error"]["type"] == "upstream_error"
                assert payload["error"]["body"] == "rate limited"

    @pytest.mark.asyncio
    async def test_upstream_error_includes_full_body(self):
        """UpstreamError body is included as-is in error dict (no truncation)."""
        long_body = "x" * 500
        exc = UpstreamError("long body error", status=502, body=long_body)
        result = await _stream_that_raises(exc)
        for line in result.split("\n"):
            if line.startswith("data: ") and "[DONE]" not in line:
                payload = orjson.loads(line[6:])
                assert payload["error"]["body"] == long_body

    @pytest.mark.asyncio
    async def test_upstream_error_with_empty_body(self):
        """UpstreamError with empty body."""
        exc = UpstreamError("no body", status=502, body="")
        result = await _stream_that_raises(exc)
        for line in result.split("\n"):
            if line.startswith("data: ") and "[DONE]" not in line:
                payload = orjson.loads(line[6:])
                assert payload["error"]["body"] == ""


class TestSafeSseAnthropicNonAppError:
    """Non-AppError exceptions → hardcoded 'api_error' type."""

    @pytest.mark.asyncio
    async def test_runtime_error_returns_api_error(self):
        """RuntimeError → inner error type is 'api_error'."""
        exc = RuntimeError("Something went terribly wrong")
        result = await _stream_that_raises(exc)
        for line in result.split("\n"):
            if line.startswith("data: ") and "[DONE]" not in line:
                payload = orjson.loads(line[6:])
                assert payload["type"] == "error"
                assert payload["error"]["type"] == "api_error"
                assert payload["error"]["message"] == "Something went terribly wrong"

    @pytest.mark.asyncio
    async def test_value_error_returns_api_error(self):
        """ValueError → inner error type is 'api_error'."""
        exc = ValueError("bad value")
        result = await _stream_that_raises(exc)
        for line in result.split("\n"):
            if line.startswith("data: ") and "[DONE]" not in line:
                payload = orjson.loads(line[6:])
                assert payload["error"]["type"] == "api_error"
                assert "bad value" in payload["error"]["message"]

    @pytest.mark.asyncio
    async def test_keyboard_interrupt_not_caught(self):
        """KeyboardInterrupt should propagate (not caught by broad 'except Exception')."""

        async def _gen():
            yield "data: before\n\n"
            raise KeyboardInterrupt()

        chunks: list[str] = []
        with pytest.raises(KeyboardInterrupt):
            async for chunk in _safe_sse_anthropic(_gen()):
                chunks.append(chunk)
        assert len(chunks) == 1  # first chunk yielded before raise


class TestSafeSseAnthropicEdgeCases:
    """Edge cases for _safe_sse_anthropic."""

    @pytest.mark.asyncio
    async def test_event_error_format(self):
        """Error event follows Anthropic SSE format: event line + data line."""
        exc = AppError("test error", kind=ErrorKind.UPSTREAM)
        result = await _stream_that_raises(exc)
        assert "event: error" in result
        # Find the data line with the error payload (skip [DONE])
        for line in result.strip().split("\n"):
            if line.startswith("data: ") and "[DONE]" not in line:
                payload = orjson.loads(line[6:])
                assert payload["type"] == "error"
                assert "error" in payload
        assert "data: [DONE]" in result

    @pytest.mark.asyncio
    async def test_first_chunk_before_error_yielded(self):
        """Chunks yielded before the exception are still delivered."""

        async def _gen():
            yield "data: before_exception\n\n"
            raise AppError("boom")

        chunks: list[str] = []
        async for chunk in _safe_sse_anthropic(_gen()):
            chunks.append(chunk)
        assert "data: before_exception" in "".join(chunks)
        assert "event: error" in "".join(chunks)

    @pytest.mark.asyncio
    async def test_exception_not_app_error(self):
        """BaseException subclass that is not Exception → propagates."""

        async def _gen():
            yield "data: ok\n\n"
            raise SystemExit(1)

        with pytest.raises(SystemExit):
            async for _ in _safe_sse_anthropic(_gen()):
                pass


class TestSafeSseAnthropicErrorKindPassthrough:
    """Specific error kind and code passthrough tests."""

    @pytest.mark.asyncio
    async def test_rate_limit_error_kind_passthrough(self):
        """RateLimitError → error.type is 'rate_limit_exceeded'."""
        from app.platform.errors import RateLimitError

        exc = RateLimitError("rate limited")
        result = await _stream_that_raises(exc)
        for line in result.split("\n"):
            if line.startswith("data: ") and "[DONE]" not in line:
                payload = orjson.loads(line[6:])
                assert payload["type"] == "error"
                assert payload["error"]["type"] == "rate_limit_exceeded"
                assert "rate limited" in payload["error"]["message"]

    @pytest.mark.asyncio
    async def test_upstream_error_with_access_denied_code(self):
        """UpstreamError with upstream_code='access_denied' → type is from to_dict()."""
        exc = UpstreamError(
            "access denied to resource",
            status=403,
            body='{"error":"access denied"}',
            upstream_code="access_denied",
            permanent_account_denial=True,
        )
        result = await _stream_that_raises(exc)
        for line in result.split("\n"):
            if line.startswith("data: ") and "[DONE]" not in line:
                payload = orjson.loads(line[6:])
                assert payload["type"] == "error"
                # upstream_code overrides the error type
                assert payload["error"]["type"] == "access_denied"
                assert payload["error"]["body"] == '{"error":"access denied"}'

    @pytest.mark.asyncio
    async def test_app_error_with_validation_kind(self):
        """AppError with kind=VALIDATION → error.type is 'invalid_request_error'."""
        exc = AppError(
            "validation failed", kind=ErrorKind.VALIDATION, code="bad_request"
        )
        result = await _stream_that_raises(exc)
        for line in result.split("\n"):
            if line.startswith("data: ") and "[DONE]" not in line:
                payload = orjson.loads(line[6:])
                assert payload["error"]["type"] == "invalid_request_error"
                assert payload["error"]["code"] == "bad_request"

    @pytest.mark.asyncio
    async def test_value_error_returns_api_error(self):
        """ValueError → error.type is 'api_error'."""
        exc = ValueError("bad value")
        result = await _stream_that_raises(exc)
        for line in result.split("\n"):
            if line.startswith("data: ") and "[DONE]" not in line:
                payload = orjson.loads(line[6:])
                assert payload["error"]["type"] == "api_error"
                assert "bad value" in payload["error"]["message"]

    @pytest.mark.asyncio
    async def test_generic_exception_returns_api_error(self):
        """Any non-AppError Exception → error.type is 'api_error'."""
        exc = PermissionError("not allowed")
        result = await _stream_that_raises(exc)
        for line in result.split("\n"):
            if line.startswith("data: ") and "[DONE]" not in line:
                payload = orjson.loads(line[6:])
                assert payload["error"]["type"] == "api_error"
                assert "not allowed" in payload["error"]["message"]

    @pytest.mark.asyncio
    async def test_data_yielded_before_error(self):
        """Data yielded before error is included AND error event is appended."""

        async def _gen():
            yield "data: hello\n\n"
            yield "data: world\n\n"
            raise AppError("midstream error", kind=ErrorKind.SERVER)

        chunks: list[str] = []
        async for chunk in _safe_sse_anthropic(_gen()):
            chunks.append(chunk)
        full = "".join(chunks)
        assert "data: hello" in full
        assert "data: world" in full
        assert "event: error" in full
        assert "data: [DONE]" in full

    @pytest.mark.asyncio
    async def test_multiple_data_lines_before_upstream_error(self):
        """Multiple data lines before UpstreamError → all delivered before error."""

        async def _gen():
            yield "data: step1\n\n"
            yield "data: step2\n\n"
            yield "data: step3\n\n"
            raise UpstreamError("upstream failed", status=502, body="bad gateway")

        chunks: list[str] = []
        async for chunk in _safe_sse_anthropic(_gen()):
            chunks.append(chunk)
        full = "".join(chunks)
        assert full.count("data: step") == 3
        assert "event: error" in full
        assert "upstream_error" in full

    @pytest.mark.asyncio
    async def test_error_after_empty_stream(self):
        """Stream that yields nothing then raises → only error events."""

        async def _gen():
            raise AppError("immediate boom", kind=ErrorKind.SERVER)
            yield  # pragma: no cover

        chunks: list[str] = []
        async for chunk in _safe_sse_anthropic(_gen()):
            chunks.append(chunk)
        full = "".join(chunks)
        assert full.startswith("event: error")
        assert "data: [DONE]" in full


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
