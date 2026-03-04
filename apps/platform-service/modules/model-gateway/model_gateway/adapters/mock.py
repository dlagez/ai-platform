from __future__ import annotations

from collections import defaultdict, deque
from typing import Iterable

from ..config import ModelConfig, ProviderConfig
from ..errors import GatewayError
from ..schemas import EmbeddingRequest, GenerationRequest
from .base import AdapterResult, BaseProviderAdapter


class MockProviderAdapter(BaseProviderAdapter):
    """Test-only adapter with scripted outcomes per model."""

    def __init__(self, scripted: dict[str, Iterable[AdapterResult | Exception]] | None = None) -> None:
        self._scripted = defaultdict(deque)
        for model_id, outcomes in (scripted or {}).items():
            self._scripted[model_id] = deque(outcomes)

    def _next_outcome(self, model_id: str, default: AdapterResult) -> AdapterResult:
        if not self._scripted[model_id]:
            return default
        outcome = self._scripted[model_id].popleft()
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def generate(
        self,
        *,
        provider: ProviderConfig,
        model: ModelConfig,
        request: GenerationRequest,
        timeout_ms: int,
    ) -> AdapterResult:
        default = AdapterResult(
            data={"content": f"mock response from {model.id}"},
            prompt_tokens=10,
            completion_tokens=20,
            total_tokens=30,
        )
        return self._next_outcome(model.id, default=default)

    async def embed(
        self,
        *,
        provider: ProviderConfig,
        model: ModelConfig,
        request: EmbeddingRequest,
        timeout_ms: int,
    ) -> AdapterResult:
        default_vectors = [[0.1, 0.2, 0.3] for _ in request.texts]
        default = AdapterResult(data={"vectors": default_vectors}, total_tokens=max(1, len(request.texts)))
        return self._next_outcome(model.id, default=default)

