from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
MODULE_ROOT = ROOT / "apps" / "platform-service" / "modules" / "ingestion"
if str(MODULE_ROOT) not in sys.path:
    sys.path.insert(0, str(MODULE_ROOT))

from ingestion.schemas import CreateIngestionJobRequest, InlineDocument, IngestionOptions, RetryIngestionJobRequest, SourceInfo
from ingestion.service import IngestionService
from ingestion.vector_store import InMemoryVectorStore


class FakeEmbeddingClient:
    async def embed(
        self,
        *,
        trace_id: str,
        request_id: str,
        app_id: str,
        texts: list[str],
        preferred_model: str | None,
    ) -> tuple[list[list[float]], str]:
        vectors = [[float(len(text)), 0.1, 0.2] for text in texts]
        return vectors, preferred_model or "fake-embed-model"


class IngestionServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.service = IngestionService(
            embedding_client=FakeEmbeddingClient(),
            vector_store=InMemoryVectorStore(),
            worker_count=1,
            retry_backoff_seconds=[1, 1, 1, 1, 1],
        )
        await self.service.start()

    async def asyncTearDown(self) -> None:
        await self.service.stop()

    async def test_create_job_and_succeed(self) -> None:
        req = CreateIngestionJobRequest(
            trace_id="t-1",
            request_id="r-1",
            tenant_id="tenant-a",
            app_id="app-a",
            source=SourceInfo(source_id="src-1", source_type="upload"),
            options=IngestionOptions(
                inline_documents=[
                    InlineDocument(
                        file_name="demo.pdf",
                        file_type="pdf",
                        content="A" * 1200,
                    )
                ]
            ),
        )

        created = await self.service.create_job(req)
        self.assertTrue(created.ok)

        # Wait for worker completion.
        for _ in range(50):
            status = await self.service.get_job(created.job_id)
            if status.status in ("SUCCEEDED", "FAILED", "DEAD_LETTER"):
                break
            await asyncio.sleep(0.05)

        status = await self.service.get_job(created.job_id)
        self.assertEqual(status.status, "SUCCEEDED")
        self.assertEqual(status.stages["parse"], "SUCCEEDED")
        self.assertEqual(status.stages["chunk"], "SUCCEEDED")
        self.assertEqual(status.stages["embed"], "SUCCEEDED")
        self.assertEqual(status.stages["index"], "SUCCEEDED")
        self.assertGreater(status.stats.chunks_total, 0)
        self.assertEqual(status.stats.chunks_total, status.stats.points_upserted)

    async def test_retry_reject_when_job_not_failed(self) -> None:
        req = CreateIngestionJobRequest(
            trace_id="t-2",
            request_id="r-2",
            tenant_id="tenant-a",
            app_id="app-a",
            source=SourceInfo(source_id="src-2", source_type="upload"),
            options=IngestionOptions(
                inline_documents=[InlineDocument(file_name="demo.txt", file_type="text", content="hello world")]
            ),
        )
        created = await self.service.create_job(req)
        await asyncio.sleep(0.1)
        with self.assertRaises(Exception):
            await self.service.retry_job(created.job_id, RetryIngestionJobRequest(from_stage="auto"))


if __name__ == "__main__":
    unittest.main()

