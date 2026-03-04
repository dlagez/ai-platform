from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GatewayError(Exception):
    code: str
    message: str
    retryable: bool
    fallbackable: bool
    provider_error: str | None = None

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "provider_error": self.provider_error,
        }


def err_no_route(message: str = "no route found") -> GatewayError:
    return GatewayError("MGW_001", message, retryable=False, fallbackable=False)


def err_provider_config(message: str = "provider auth/config invalid") -> GatewayError:
    return GatewayError("MGW_002", message, retryable=False, fallbackable=True)


def err_deadline(
    message: str = "request deadline exceeded",
    *,
    retryable: bool = False,
    fallbackable: bool = False,
) -> GatewayError:
    return GatewayError("MGW_003", message, retryable=retryable, fallbackable=fallbackable)


def err_all_fallback_failed(provider_error: str | None = None) -> GatewayError:
    return GatewayError(
        "MGW_004",
        "all fallback models failed",
        retryable=True,
        fallbackable=False,
        provider_error=provider_error,
    )


def err_rate_limited(provider_error: str | None = None) -> GatewayError:
    return GatewayError(
        "MGW_005",
        "provider rate limited",
        retryable=True,
        fallbackable=True,
        provider_error=provider_error,
    )


def err_invalid_params(message: str = "invalid request params") -> GatewayError:
    return GatewayError("MGW_006", message, retryable=False, fallbackable=False)


def err_circuit_open(message: str = "circuit open") -> GatewayError:
    return GatewayError("MGW_007", message, retryable=False, fallbackable=True)


def err_adapter_internal(provider_error: str | None = None) -> GatewayError:
    return GatewayError(
        "MGW_008",
        "internal adapter error",
        retryable=True,
        fallbackable=True,
        provider_error=provider_error,
    )
