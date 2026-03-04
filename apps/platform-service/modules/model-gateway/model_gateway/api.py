from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException

from .gateway import ModelGateway
from .schemas import EmbeddingRequest, GatewayResponse, GenerationRequest


logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger("model_gateway_api")

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[5] / "configs" / "env" / "model-gateway.yaml"


class _GatewayHolder:
    gateway: ModelGateway | None = None
    config_path: str | None = None

    @classmethod
    def get_gateway(cls) -> ModelGateway:
        config_path = os.getenv("MODEL_GATEWAY_CONFIG", str(DEFAULT_CONFIG_PATH))
        if cls.gateway is None or cls.config_path != config_path:
            cls.gateway = ModelGateway.from_config_file(config_path)
            cls.config_path = config_path
            LOGGER.info("model gateway initialized, config=%s", config_path)
        return cls.gateway


app = FastAPI(title="Model Gateway", version="0.1.0")


@app.get("/internal/model-gateway/healthz")
async def healthz() -> dict:
    try:
        gateway = _GatewayHolder.get_gateway()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"gateway init failed: {exc}") from exc
    return {
        "status": "ok",
        "providers": sorted(gateway.config_store.providers.keys()),
        "models": sorted(gateway.config_store.models_by_id.keys()),
    }


@app.post("/internal/model-gateway/generate", response_model=GatewayResponse)
async def generate(payload: GenerationRequest) -> GatewayResponse:
    try:
        gateway = _GatewayHolder.get_gateway()
        return await gateway.generate(payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"generate failed: {exc}") from exc


@app.post("/internal/model-gateway/embed", response_model=GatewayResponse)
async def embed(payload: EmbeddingRequest) -> GatewayResponse:
    try:
        gateway = _GatewayHolder.get_gateway()
        return await gateway.embed(payload)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"embed failed: {exc}") from exc


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("model_gateway.api:app", host="0.0.0.0", port=8081, reload=False)

