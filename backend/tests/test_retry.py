import pytest

from app.infra.retry import call_with_retry


@pytest.mark.anyio
async def test_non_transient_error_is_not_retried():
    attempts = 0

    async def invalid_call():
        nonlocal attempts
        attempts += 1
        raise ValueError("invalid MCP arguments")

    with pytest.raises(ValueError):
        await call_with_retry(invalid_call, attempts=3, base_seconds=0, max_seconds=0)
    assert attempts == 1


@pytest.mark.anyio
async def test_http_429_is_retried():
    attempts = 0

    class _RateLimitError(Exception):
        status_code = 429

    async def retry_once():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise _RateLimitError("rate limited")
        return "ok"

    assert await call_with_retry(retry_once, attempts=2, base_seconds=0, max_seconds=0) == "ok"
    assert attempts == 2
