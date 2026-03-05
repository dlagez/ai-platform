from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[3]
MODULE_ROOT = ROOT / "apps" / "platform-service" / "modules" / "ingestion"
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from ingestion.schemas import PointPayload, VectorPoint
from ingestion.service import IngestionService
from ingestion.vector_store import LocalQdrantVectorStore, QdrantVectorStore


class _DummyEmbeddingClient:
    async def embed(
        self,
        *,
        trace_id: str,
        request_id: str,
        app_id: str,
        texts: list[str],
        preferred_model: str | None,
    ) -> tuple[list[list[float]], str]:
        return [[0.1, 0.2, 0.3] for _ in texts], preferred_model or "dummy"


def _write_ingestion_config(
    *,
    base_dir: str,
    qdrant_url: str = "",
    qdrant_api_key: str = "",
    qdrant_path: str = "",
) -> str:
    path = Path(base_dir) / "ingestion.yaml"
    payload = {
        "vector_store": {
            "qdrant_url": qdrant_url,
            "qdrant_api_key": qdrant_api_key,
            "qdrant_path": qdrant_path,
        }
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return str(path)


class LocalQdrantVectorStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_qdrant_store_upsert_points(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = LocalQdrantVectorStore(path=tmp_dir)
            point = VectorPoint(
                id="chunk_1",
                vector=[0.1, 0.2, 0.3],
                payload=PointPayload(
                    ingest_job_id="ing_1",
                    tenant_id="tenant-a",
                    app_id="app-a",
                    source_id="src-1",
                    source_type="upload",
                    doc_id="doc-1",
                    file_name="a.txt",
                    file_type="text",
                    version_hash="vh_1",
                    chunk_source_ref="text:L1",
                    chunk_order=0,
                ),
            )

            written = await store.upsert_points(collection="col_test", points=[point], vector_size=3)
            self.assertEqual(written, 1)

            count = store.client.count(collection_name="col_test", exact=True).count
            self.assertEqual(count, 1)
            await store.close()


class VectorStoreSelectionTests(unittest.TestCase):
    def test_default_vector_store_uses_local_qdrant_from_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            local_path = str(Path(tmp_dir) / "qdrant_local")
            ingestion_config = _write_ingestion_config(base_dir=tmp_dir, qdrant_path=local_path)
            with patch.dict(
                os.environ,
                {
                    "INGESTION_CONFIG": ingestion_config,
                    "QDRANT_URL": "",
                    "QDRANT_API_KEY": "",
                    "QDRANT_PATH": "",
                },
                clear=False,
            ):
                service = IngestionService(embedding_client=_DummyEmbeddingClient())
                self.assertIsInstance(service.vector_store, LocalQdrantVectorStore)
                asyncio.run(service.vector_store.close())

    def test_default_vector_store_uses_local_qdrant_when_path_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            ingestion_config = _write_ingestion_config(base_dir=tmp_dir, qdrant_path="")
            with patch.dict(
                os.environ,
                {
                    "INGESTION_CONFIG": ingestion_config,
                    "QDRANT_URL": "",
                    "QDRANT_PATH": tmp_dir,
                },
                clear=False,
            ):
                service = IngestionService(embedding_client=_DummyEmbeddingClient())
                self.assertIsInstance(service.vector_store, LocalQdrantVectorStore)
                asyncio.run(service.vector_store.close())

    def test_default_vector_store_prioritizes_qdrant_url_over_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            ingestion_config = _write_ingestion_config(base_dir=tmp_dir, qdrant_path=tmp_dir)
            with patch.dict(
                os.environ,
                {
                    "INGESTION_CONFIG": ingestion_config,
                    "QDRANT_URL": "http://127.0.0.1:6333",
                    "QDRANT_API_KEY": "",
                    "QDRANT_PATH": tmp_dir,
                },
                clear=False,
            ):
                service = IngestionService(embedding_client=_DummyEmbeddingClient())
                self.assertIsInstance(service.vector_store, QdrantVectorStore)
                asyncio.run(service.vector_store.close())

    def test_relative_qdrant_path_in_config_resolves_from_repo_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            ingestion_config = _write_ingestion_config(base_dir=tmp_dir, qdrant_path="index/custom_local")
            with patch.dict(
                os.environ,
                {
                    "INGESTION_CONFIG": ingestion_config,
                    "QDRANT_URL": "",
                    "QDRANT_API_KEY": "",
                    "QDRANT_PATH": "",
                },
                clear=False,
            ):
                _, _, qdrant_path = IngestionService._resolve_vector_store_settings()
                self.assertEqual(qdrant_path, str(ROOT / "index" / "custom_local"))

if __name__ == "__main__":
    unittest.main()
