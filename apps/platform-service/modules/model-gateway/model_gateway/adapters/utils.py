from __future__ import annotations

import asyncio

import httpx

from ..errors import (
    GatewayError,
    err_adapter_internal,
    err_deadline,
    err_invalid_params,
    err_provider_config,
    err_rate_limited,
)


def classify_provider_exception(exc: Exception) -> GatewayError:
    if isinstance(exc, GatewayError):
        return exc

    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, httpx.TimeoutException)):
        return err_deadline(str(exc) or "provider timeout", retryable=True, fallbackable=True)

    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 429:
            return err_rate_limited(str(exc))
        if status in (401, 403):
            return err_provider_config(str(exc))
        if status >= 500:
            return err_adapter_internal(str(exc))
        return err_invalid_params(str(exc))

    if isinstance(exc, httpx.RequestError):
        return err_adapter_internal(str(exc))

    message = str(exc).lower()
    if "rate limit" in message:
        return err_rate_limited(str(exc))
    if "api key" in message or "unauthorized" in message or "forbidden" in message:
        return err_provider_config(str(exc))
    if "timeout" in message:
        return err_deadline(str(exc), retryable=True, fallbackable=True)
    return err_adapter_internal(str(exc))
