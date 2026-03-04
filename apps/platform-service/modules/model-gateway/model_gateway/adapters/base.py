from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from ..config import ModelConfig, ProviderConfig
from ..schemas import EmbeddingRequest, GenerationRequest


@dataclass
class AdapterResult:
    data: dict[str, Any]
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class BaseProviderAdapter(ABC):
    @abstractmethod
    async def generate(
        self,
        *,
        provider: ProviderConfig,
        model: ModelConfig,
        request: GenerationRequest,
        timeout_ms: int,
    ) -> AdapterResult:
        raise NotImplementedError

    @abstractmethod
    async def embed(
        self,
        *,
        provider: ProviderConfig,
        model: ModelConfig,
        request: EmbeddingRequest,
        timeout_ms: int,
    ) -> AdapterResult:
        raise NotImplementedError

