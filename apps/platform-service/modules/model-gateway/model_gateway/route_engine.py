from __future__ import annotations

from .config import ConfigStore
from .circuit_breaker import CircuitBreaker, CircuitState


def breaker_key(provider: str, model: str) -> str:
    return f"{provider}:{model}"


class RouteEngine:
    def __init__(self, config_store: ConfigStore, breaker: CircuitBreaker) -> None:
        self.config_store = config_store
        self.breaker = breaker

    def get_candidates(self, scene: str, task: str, preferred_model: str | None = None) -> list[str]:
        ordered = list(self.config_store.list_candidates(scene=scene, task=task))

        if preferred_model:
            model_cfg = self.config_store.models_by_id.get(preferred_model)
            if model_cfg and model_cfg.enabled and model_cfg.task == task:
                ordered.insert(0, preferred_model)

        deduped: list[str] = []
        seen: set[str] = set()
        for model_id in ordered:
            if model_id not in seen:
                seen.add(model_id)
                deduped.append(model_id)

        available: list[str] = []
        for model_id in deduped:
            model_cfg = self.config_store.get_model(model_id)
            key = breaker_key(model_cfg.provider, model_cfg.id)
            state = self.breaker.state(key)
            if state != CircuitState.OPEN:
                available.append(model_id)

        return available

