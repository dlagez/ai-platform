from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from typing import Literal

from .adapters import BaseProviderAdapter, LangChainAnthropicAdapter, LangChainOpenAIAdapter
from .adapters.base import AdapterResult
from .config import ConfigStore, ModelConfig, ProviderConfig, load_gateway_config
from .circuit_breaker import CircuitBreaker, CircuitBreakerSettings
from .errors import (
    GatewayError,
    err_adapter_internal,
    err_all_fallback_failed,
    err_circuit_open,
    err_deadline,
    err_no_route,
    err_provider_config,
)
from .route_engine import RouteEngine, breaker_key
from .schemas import (
    AttemptTrace,
    EmbeddingRequest,
    GatewayErrorDetail,
    GatewayResponse,
    GenerationRequest,
    UsageStats,
)


class ModelGateway:
    def __init__(
        self,
        *,
        config_store: ConfigStore,
        adapters: dict[str, BaseProviderAdapter],
        breaker: CircuitBreaker | None = None,
        max_retries_per_model: int = 1,
        retry_backoff_ms: int = 200,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config_store = config_store
        self.adapters = adapters
        self.breaker = breaker or CircuitBreaker()
        self.route_engine = RouteEngine(config_store, self.breaker)
        self.max_retries_per_model = max_retries_per_model
        self.retry_backoff_ms = retry_backoff_ms
        self.logger = logger or logging.getLogger("model_gateway")

    @classmethod
    def from_config_file(
        cls,
        config_path: str | Path,
        *,
        breaker_settings: CircuitBreakerSettings | None = None,
    ) -> "ModelGateway":
        config_store = load_gateway_config(config_path)
        adapters: dict[str, BaseProviderAdapter] = {}

        # Register default adapters by provider name.
        for provider_name in config_store.providers:
            if provider_name == "openai":
                adapters[provider_name] = LangChainOpenAIAdapter()
            elif provider_name == "anthropic":
                adapters[provider_name] = LangChainAnthropicAdapter()

        return cls(
            config_store=config_store,
            adapters=adapters,
            breaker=CircuitBreaker(settings=breaker_settings),
        )

    async def generate(self, request: GenerationRequest) -> GatewayResponse:
        return await self._execute(task="generation", request=request)

    async def embed(self, request: EmbeddingRequest) -> GatewayResponse:
        return await self._execute(task="embedding", request=request)

    async def _execute(
        self,
        *,
        task: Literal["generation", "embedding"],
        request: GenerationRequest | EmbeddingRequest,
    ) -> GatewayResponse:
        started_at = time.monotonic()
        attempt_chain: list[AttemptTrace] = []
        total_attempts = 0
        attempted_models = 0
        last_error: GatewayError | None = None

        candidates = self.route_engine.get_candidates(
            scene=request.scene,
            task=task,
            preferred_model=request.routing.preferred_model,
        )
        if not candidates:
            return self._failure_response(
                err_no_route(),
                started_at=started_at,
                attempts=0,
                fallback_used=False,
                attempt_chain=attempt_chain,
            )

        max_models_to_try = 1 if not request.routing.allow_fallback else 1 + request.routing.max_fallback_hops
        selected_models = candidates[:max_models_to_try]

        for model_id in selected_models:
            model_cfg = self.config_store.get_model(model_id)
            provider_cfg = self.config_store.get_provider(model_cfg.provider)
            provider_adapter = self.adapters.get(model_cfg.provider)
            if provider_adapter is None:
                last_error = err_provider_config(f"adapter for provider '{model_cfg.provider}' not configured")
                if not request.routing.allow_fallback:
                    break
                continue

            key = breaker_key(model_cfg.provider, model_cfg.id)
            if not self.breaker.allow_request(key):
                total_attempts += 1
                last_error = err_circuit_open()
                attempt_chain.append(
                    AttemptTrace(
                        attempt=total_attempts,
                        provider=model_cfg.provider,
                        model=model_cfg.id,
                        status="skipped",
                        latency_ms=0,
                        error_code=last_error.code,
                        fallback_reason="circuit_open",
                    )
                )
                if not request.routing.allow_fallback:
                    break
                continue

            attempted_models += 1
            result_or_error, used_attempts = await self._invoke_with_retry(
                task=task,
                request=request,
                model_cfg=model_cfg,
                provider_cfg=provider_cfg,
                provider_adapter=provider_adapter,
                started_at=started_at,
                attempt_offset=total_attempts,
                attempt_chain=attempt_chain,
            )
            total_attempts += used_attempts

            if isinstance(result_or_error, GatewayResponse):
                result_or_error.attempts = total_attempts
                result_or_error.fallback_used = self._fallback_used(attempt_chain)
                return result_or_error

            last_error = result_or_error
            if (not request.routing.allow_fallback) or (not last_error.fallbackable):
                break

        if last_error is None:
            last_error = err_all_fallback_failed()
        elif request.routing.allow_fallback and attempted_models > 1 and last_error.code != "MGW_003":
            last_error = err_all_fallback_failed(provider_error=last_error.provider_error or last_error.message)

        return self._failure_response(
            last_error,
            started_at=started_at,
            attempts=total_attempts,
            fallback_used=self._fallback_used(attempt_chain),
            attempt_chain=attempt_chain,
            final_model=attempt_chain[-1].model if attempt_chain else None,
            final_provider=attempt_chain[-1].provider if attempt_chain else None,
        )

    async def _invoke_with_retry(
        self,
        *,
        task: Literal["generation", "embedding"],
        request: GenerationRequest | EmbeddingRequest,
        model_cfg: ModelConfig,
        provider_cfg: ProviderConfig,
        provider_adapter: BaseProviderAdapter,
        started_at: float,
        attempt_offset: int,
        attempt_chain: list[AttemptTrace],
    ) -> tuple[GatewayResponse | GatewayError, int]:
        key = breaker_key(model_cfg.provider, model_cfg.id)
        attempts_used = 0

        for retry_idx in range(self.max_retries_per_model + 1):
            attempts_used += 1
            attempt_no = attempt_offset + attempts_used
            remaining = self._remaining_ms(started_at, request.deadline_ms)
            if remaining <= 500:
                deadline_error = err_deadline()
                attempt_chain.append(
                    AttemptTrace(
                        attempt=attempt_no,
                        provider=model_cfg.provider,
                        model=model_cfg.id,
                        status="failed",
                        latency_ms=0,
                        error_code=deadline_error.code,
                        fallback_reason="timeout",
                    )
                )
                return deadline_error, attempts_used

            timeout_ms = min(provider_cfg.timeout_ms, max(remaining - 500, 1))
            invoke_start = time.monotonic()
            try:
                result = await asyncio.wait_for(
                    self._invoke_adapter(
                        task=task,
                        provider_adapter=provider_adapter,
                        provider_cfg=provider_cfg,
                        model_cfg=model_cfg,
                        request=request,
                        timeout_ms=timeout_ms,
                    ),
                    timeout=max(timeout_ms / 1000.0, 0.001),
                )
                latency_ms = int((time.monotonic() - invoke_start) * 1000)
                self.breaker.record_result(key, success=True)
                usage = self._build_usage(model_cfg, result)
                attempt_chain.append(
                    AttemptTrace(
                        attempt=attempt_no,
                        provider=model_cfg.provider,
                        model=model_cfg.id,
                        status="success",
                        latency_ms=latency_ms,
                    )
                )
                self._log_attempt(
                    trace_id=request.trace_id,
                    request_id=request.request_id,
                    scene=request.scene,
                    task=task,
                    provider=model_cfg.provider,
                    model=model_cfg.id,
                    attempt=attempt_no,
                    latency_ms=latency_ms,
                    error_code=None,
                )
                return (
                    GatewayResponse(
                        ok=True,
                        provider=model_cfg.provider,
                        model=model_cfg.id,
                        usage=usage,
                        latency_ms=int((time.monotonic() - started_at) * 1000),
                        data=result.data,
                        attempt_chain=attempt_chain,
                        final_model=model_cfg.id,
                        final_provider=model_cfg.provider,
                    ),
                    attempts_used,
                )
            except GatewayError as err:
                latency_ms = int((time.monotonic() - invoke_start) * 1000)
                self.breaker.record_result(key, success=False)
                fallback_reason = self._fallback_reason(err)
                attempt_chain.append(
                    AttemptTrace(
                        attempt=attempt_no,
                        provider=model_cfg.provider,
                        model=model_cfg.id,
                        status="failed",
                        latency_ms=latency_ms,
                        error_code=err.code,
                        fallback_reason=fallback_reason,
                    )
                )
                self._log_attempt(
                    trace_id=request.trace_id,
                    request_id=request.request_id,
                    scene=request.scene,
                    task=task,
                    provider=model_cfg.provider,
                    model=model_cfg.id,
                    attempt=attempt_no,
                    latency_ms=latency_ms,
                    error_code=err.code,
                )
                should_retry = (
                    retry_idx < self.max_retries_per_model
                    and err.retryable
                    and self._remaining_ms(started_at, request.deadline_ms) > 500
                )
                if should_retry:
                    await asyncio.sleep(self.retry_backoff_ms / 1000.0)
                    continue
                return err, attempts_used
            except Exception as exc:
                adapter_error = err_adapter_internal(str(exc))
                latency_ms = int((time.monotonic() - invoke_start) * 1000)
                self.breaker.record_result(key, success=False)
                attempt_chain.append(
                    AttemptTrace(
                        attempt=attempt_no,
                        provider=model_cfg.provider,
                        model=model_cfg.id,
                        status="failed",
                        latency_ms=latency_ms,
                        error_code=adapter_error.code,
                        fallback_reason="provider_unavailable",
                    )
                )
                self._log_attempt(
                    trace_id=request.trace_id,
                    request_id=request.request_id,
                    scene=request.scene,
                    task=task,
                    provider=model_cfg.provider,
                    model=model_cfg.id,
                    attempt=attempt_no,
                    latency_ms=latency_ms,
                    error_code=adapter_error.code,
                )
                return adapter_error, attempts_used

        return err_all_fallback_failed(), attempts_used

    async def _invoke_adapter(
        self,
        *,
        task: Literal["generation", "embedding"],
        provider_adapter: BaseProviderAdapter,
        provider_cfg: ProviderConfig,
        model_cfg: ModelConfig,
        request: GenerationRequest | EmbeddingRequest,
        timeout_ms: int,
    ) -> AdapterResult:
        if task == "generation":
            return await provider_adapter.generate(
                provider=provider_cfg,
                model=model_cfg,
                request=request,  # type: ignore[arg-type]
                timeout_ms=timeout_ms,
            )
        return await provider_adapter.embed(
            provider=provider_cfg,
            model=model_cfg,
            request=request,  # type: ignore[arg-type]
            timeout_ms=timeout_ms,
        )

    @staticmethod
    def _remaining_ms(started_at: float, deadline_ms: int) -> int:
        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        return deadline_ms - elapsed_ms

    @staticmethod
    def _build_usage(model_cfg: ModelConfig, result: AdapterResult) -> UsageStats:
        prompt_cost = (result.prompt_tokens / 1000.0) * model_cfg.input_price_per_1k
        completion_cost = (result.completion_tokens / 1000.0) * model_cfg.output_price_per_1k
        if result.completion_tokens == 0 and result.total_tokens > 0:
            prompt_cost = (result.total_tokens / 1000.0) * model_cfg.input_price_per_1k
        return UsageStats(
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            total_tokens=result.total_tokens,
            estimated_cost=round(prompt_cost + completion_cost, 8),
            currency="USD",
        )

    @staticmethod
    def _fallback_reason(err: GatewayError) -> str | None:
        return {
            "MGW_003": "timeout",
            "MGW_005": "rate_limited",
            "MGW_007": "circuit_open",
            "MGW_008": "provider_unavailable",
        }.get(err.code)

    def _failure_response(
        self,
        err: GatewayError,
        *,
        started_at: float,
        attempts: int,
        fallback_used: bool,
        attempt_chain: list[AttemptTrace],
        final_model: str | None = None,
        final_provider: str | None = None,
    ) -> GatewayResponse:
        return GatewayResponse(
            ok=False,
            attempts=attempts,
            fallback_used=fallback_used,
            latency_ms=int((time.monotonic() - started_at) * 1000),
            error=GatewayErrorDetail(
                code=err.code,
                message=err.message,
                retryable=err.retryable,
                provider_error=err.provider_error,
            ),
            attempt_chain=attempt_chain,
            final_model=final_model,
            final_provider=final_provider,
        )

    def _log_attempt(
        self,
        *,
        trace_id: str,
        request_id: str,
        scene: str,
        task: str,
        provider: str,
        model: str,
        attempt: int,
        latency_ms: int,
        error_code: str | None,
    ) -> None:
        self.logger.info(
            "model_gateway_attempt trace_id=%s request_id=%s scene=%s task=%s provider=%s model=%s attempt=%s latency_ms=%s error_code=%s",
            trace_id,
            request_id,
            scene,
            task,
            provider,
            model,
            attempt,
            latency_ms,
            error_code or "",
        )

    @staticmethod
    def _fallback_used(attempt_chain: list[AttemptTrace]) -> bool:
        return len({(item.provider, item.model) for item in attempt_chain}) > 1
