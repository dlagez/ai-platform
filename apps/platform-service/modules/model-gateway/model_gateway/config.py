from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field

from .errors import err_invalid_params


ENV_PATTERN = re.compile(r"\$\{([A-Z0-9_]+)\}")


class ProviderConfig(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    timeout_ms: int = Field(default=10000, ge=500, le=120000)


class ModelConfig(BaseModel):
    id: str
    provider: str
    task: Literal["generation", "embedding"]
    enabled: bool = True
    priority: int = 10
    max_context_tokens: int | None = None
    input_price_per_1k: float = 0.0
    output_price_per_1k: float = 0.0


class SceneRouteConfig(BaseModel):
    generation: list[str] = Field(default_factory=list)
    embedding: list[str] = Field(default_factory=list)


class GatewayConfig(BaseModel):
    providers: dict[str, ProviderConfig]
    models: list[ModelConfig]
    routes: dict[str, SceneRouteConfig]


@dataclass
class ConfigStore:
    config: GatewayConfig

    def __post_init__(self) -> None:
        self.providers = self.config.providers
        self.models_by_id = {m.id: m for m in self.config.models}
        self._validate_routes()

    providers: dict[str, ProviderConfig] = None
    models_by_id: dict[str, ModelConfig] = None

    def _validate_routes(self) -> None:
        for scene, route in self.config.routes.items():
            for task in ("generation", "embedding"):
                model_ids = getattr(route, task)
                for model_id in model_ids:
                    model_cfg = self.models_by_id.get(model_id)
                    if model_cfg is None:
                        raise err_invalid_params(f"route '{scene}.{task}' references unknown model '{model_id}'")
                    if not model_cfg.enabled:
                        raise err_invalid_params(f"route '{scene}.{task}' references disabled model '{model_id}'")
                    if model_cfg.task != task:
                        raise err_invalid_params(
                            f"route '{scene}.{task}' has model '{model_id}' with task '{model_cfg.task}'"
                        )

    def get_provider(self, provider_name: str) -> ProviderConfig:
        provider = self.providers.get(provider_name)
        if provider is None:
            raise err_invalid_params(f"provider '{provider_name}' not found")
        return provider

    def get_model(self, model_id: str) -> ModelConfig:
        model = self.models_by_id.get(model_id)
        if model is None:
            raise err_invalid_params(f"model '{model_id}' not found")
        return model

    def list_candidates(self, scene: str, task: str) -> list[str]:
        route = self.config.routes.get(scene)
        if route is None:
            return []
        candidates = list(getattr(route, task))
        return [m for m in candidates if self.models_by_id[m].enabled]


def _resolve_env(value: object) -> object:
    if isinstance(value, str):
        matches = ENV_PATTERN.findall(value)
        for key in matches:
            value = value.replace(f"${{{key}}}", os.getenv(key, ""))
        return value
    if isinstance(value, list):
        return [_resolve_env(item) for item in value]
    if isinstance(value, dict):
        return {k: _resolve_env(v) for k, v in value.items()}
    return value


def load_gateway_config(config_path: str | Path) -> ConfigStore:
    path = Path(config_path)
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    resolved = _resolve_env(raw)
    cfg = GatewayConfig.model_validate(resolved)
    return ConfigStore(cfg)

