from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MODULE_ROOT = ROOT / "apps" / "platform-service" / "modules" / "model-gateway"
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from model_gateway.adapters.base import AdapterResult
from model_gateway.adapters.langchain_openai import LangChainOpenAIAdapter
from model_gateway.adapters.mock import MockProviderAdapter
from model_gateway.config import ConfigStore, GatewayConfig, ModelConfig, ProviderConfig, SceneRouteConfig
from model_gateway.circuit_breaker import CircuitBreaker, CircuitBreakerSettings
from model_gateway.errors import GatewayError, err_deadline
from model_gateway.gateway import ModelGateway
from model_gateway.schemas import EmbeddingRequest, GenerationRequest, Message, RoutingOptions


def build_gateway(adapter: MockProviderAdapter | None = None) -> ModelGateway:
    cfg = GatewayConfig(
        providers={"mock": ProviderConfig(timeout_ms=3000)},
        models=[
            ModelConfig(id="primary-model", provider="mock", task="generation", enabled=True, priority=10),
            ModelConfig(id="backup-model", provider="mock", task="generation", enabled=True, priority=20),
            ModelConfig(id="embed-model", provider="mock", task="embedding", enabled=True, priority=10),
        ],
        routes={
            "rag_qa": SceneRouteConfig(generation=["primary-model", "backup-model"]),
            "ingestion": SceneRouteConfig(embedding=["embed-model"]),
        },
    )
    breaker = CircuitBreaker(
        CircuitBreakerSettings(
            sliding_window_seconds=60,
            min_requests=20,
            failure_rate_threshold=0.5,
            open_seconds=10,
            half_open_probe_requests=5,
            half_open_success_threshold=0.8,
        )
    )
    return ModelGateway(
        config_store=ConfigStore(cfg),
        adapters={"mock": adapter or MockProviderAdapter()},
        breaker=breaker,
        max_retries_per_model=1,
        retry_backoff_ms=0,
    )


class ModelGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_primary_success(self) -> None:
        gateway = build_gateway()
        req = GenerationRequest(
            trace_id="t-1",
            request_id="r-1",
            app_id="app",
            scene="rag_qa",
            messages=[Message(role="user", content="hello")],
        )
        resp = await gateway.generate(req)
        self.assertTrue(resp.ok)
        self.assertEqual(resp.model, "primary-model")
        self.assertEqual(resp.attempts, 1)
        self.assertFalse(resp.fallback_used)
        self.assertEqual(len(resp.attempt_chain), 1)

    async def test_fallback_after_primary_timeout(self) -> None:
        adapter = MockProviderAdapter(
            scripted={
                "primary-model": [
                    err_deadline("provider timeout", retryable=True, fallbackable=True),
                    err_deadline("provider timeout", retryable=True, fallbackable=True),
                ],
                "backup-model": [AdapterResult(data={"content": "backup ok"}, prompt_tokens=8, completion_tokens=4, total_tokens=12)],
            }
        )
        gateway = build_gateway(adapter=adapter)
        req = GenerationRequest(
            trace_id="t-2",
            request_id="r-2",
            app_id="app",
            scene="rag_qa",
            messages=[Message(role="user", content="hello")],
            routing=RoutingOptions(allow_fallback=True, max_fallback_hops=2),
        )
        resp = await gateway.generate(req)
        self.assertTrue(resp.ok)
        self.assertEqual(resp.model, "backup-model")
        self.assertTrue(resp.fallback_used)
        self.assertGreaterEqual(resp.attempts, 3)
        self.assertEqual(resp.attempt_chain[-1].status, "success")

    async def test_no_fallback_when_disabled(self) -> None:
        adapter = MockProviderAdapter(
            scripted={
                "primary-model": [
                    err_deadline("provider timeout", retryable=True, fallbackable=True),
                    err_deadline("provider timeout", retryable=True, fallbackable=True),
                ]
            }
        )
        gateway = build_gateway(adapter=adapter)
        req = GenerationRequest(
            trace_id="t-3",
            request_id="r-3",
            app_id="app",
            scene="rag_qa",
            messages=[Message(role="user", content="hello")],
            routing=RoutingOptions(allow_fallback=False, max_fallback_hops=0),
        )
        resp = await gateway.generate(req)
        self.assertFalse(resp.ok)
        self.assertIsNotNone(resp.error)
        self.assertEqual(resp.error.code, "MGW_003")
        self.assertFalse(resp.fallback_used)

    async def test_embedding_success(self) -> None:
        gateway = build_gateway()
        req = EmbeddingRequest(
            trace_id="t-4",
            request_id="r-4",
            app_id="app",
            texts=["hello", "world"],
        )
        resp = await gateway.embed(req)
        self.assertTrue(resp.ok)
        self.assertEqual(resp.model, "embed-model")
        self.assertIn("vectors", resp.data)


class ModelGatewayConfigLoadingTests(unittest.TestCase):
    def test_from_config_file_supports_custom_openai_compatible_provider(self) -> None:
        config_text = """
providers:
  openai:
    base_url: https://dashscope.aliyuncs.com/compatible-mode/v1/responses
    api_key: openai-key
    timeout_ms: 10000
  local_emb:
    adapter: openai_compatible
    base_url: http://127.0.0.1:8002/v1
    api_key: dummy-local-key
    timeout_ms: 10000

models:
  - id: qwen3.5-flash
    provider: openai
    task: generation
    enabled: true
  - id: bge-m3
    provider: local_emb
    task: embedding
    enabled: true

routes:
  ingestion:
    generation: []
    embedding:
      - bge-m3
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "gateway.yaml"
            path.write_text(config_text, encoding="utf-8")
            gateway = ModelGateway.from_config_file(path)

        self.assertIn("local_emb", gateway.adapters)
        self.assertIsInstance(gateway.adapters["local_emb"], LangChainOpenAIAdapter)

    def test_from_config_file_keeps_legacy_openai_mapping(self) -> None:
        config_text = """
providers:
  openai:
    base_url: https://dashscope.aliyuncs.com/compatible-mode/v1/responses
    api_key: openai-key
    timeout_ms: 10000

models:
  - id: qwen3.5-flash
    provider: openai
    task: generation
    enabled: true

routes:
  rag_qa:
    generation:
      - qwen3.5-flash
    embedding: []
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "gateway.yaml"
            path.write_text(config_text, encoding="utf-8")
            gateway = ModelGateway.from_config_file(path)

        self.assertIn("openai", gateway.adapters)
        self.assertIsInstance(gateway.adapters["openai"], LangChainOpenAIAdapter)

    def test_from_config_file_requires_adapter_for_custom_provider(self) -> None:
        config_text = """
providers:
  local_emb:
    base_url: http://127.0.0.1:8002/v1
    api_key: dummy-local-key
    timeout_ms: 10000

models:
  - id: bge-m3
    provider: local_emb
    task: embedding
    enabled: true

routes:
  ingestion:
    generation: []
    embedding:
      - bge-m3
"""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "gateway.yaml"
            path.write_text(config_text, encoding="utf-8")
            with self.assertRaises(GatewayError) as exc:
                ModelGateway.from_config_file(path)

        self.assertIn("adapter is not configured", str(exc.exception))


if __name__ == "__main__":
    unittest.main()
