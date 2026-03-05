from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Protocol

from .errors import err_embedding_failed


class EmbeddingClient(Protocol):
    async def embed(
        self,
        *,
        trace_id: str,
        request_id: str,
        app_id: str,
        texts: list[str],
        preferred_model: str | None,
    ) -> tuple[list[list[float]], str]:
        """Returns vectors and the model id used."""


class ModelGatewayEmbeddingClient:
    def __init__(self, config_path: str | None = None) -> None:
        module_root = Path(__file__).resolve().parents[2] / "model-gateway"
        if str(module_root) not in sys.path:
            sys.path.insert(0, str(module_root))

        # Import after path mutation.
        from model_gateway.gateway import ModelGateway
        from model_gateway.schemas import EmbeddingRequest, RoutingOptions

        self._EmbeddingRequest = EmbeddingRequest
        self._RoutingOptions = RoutingOptions
        self._gateway = ModelGateway.from_config_file(
            config_path
            or os.getenv(
                "MODEL_GATEWAY_CONFIG",
                str(Path(__file__).resolve().parents[5] / "configs" / "env" / "model-gateway.yaml"),
            )
        )

    async def embed(
        self,
        *,
        trace_id: str,
        request_id: str,
        app_id: str,
        texts: list[str],
        preferred_model: str | None,
    ) -> tuple[list[list[float]], str]:
        req = self._EmbeddingRequest(
            trace_id=trace_id,
            request_id=request_id,
            app_id=app_id,
            texts=texts,
            routing=self._RoutingOptions(
                preferred_model=preferred_model,
                allow_fallback=True,
                max_fallback_hops=1,
            ),
            deadline_ms=15000,
        )
        resp = await self._gateway.embed(req)
        if not resp.ok:
            code = resp.error.code if resp.error else "IGT_007"
            retryable = bool(resp.error.retryable) if resp.error else True
            raise err_embedding_failed(f"model gateway embed failed: {code}", retryable=retryable)
        vectors = (resp.data or {}).get("vectors")
        if not isinstance(vectors, list):
            raise err_embedding_failed("model gateway embed returned invalid vectors", retryable=False)
        model = resp.model or resp.final_model or "unknown-model"
        return vectors, model

