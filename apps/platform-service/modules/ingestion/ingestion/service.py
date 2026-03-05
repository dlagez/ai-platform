from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .embedding_client import EmbeddingClient, ModelGatewayEmbeddingClient
from .errors import (
    IngestionError,
    err_chunk_failed,
    err_embedding_failed,
    err_invalid_params,
    err_job_state_conflict,
    err_parse_failed,
    err_retry_exhausted,
)
from .schemas import (
    ChunkArtifact,
    CreateIngestionJobRequest,
    CreateIngestionJobResponse,
    IngestionJobRecord,
    IngestionJobStatusResponse,
    JobStatus,
    ParsedDocument,
    PointPayload,
    RetryIngestionJobRequest,
    StageName,
    StageStatus,
    VectorPoint,
)
from .store import InMemoryJobStore
from .vector_store import BaseVectorStore, InMemoryVectorStore, QdrantVectorStore, build_collection_name


LOGGER = logging.getLogger("ingestion_service")


@dataclass
class JobArtifacts:
    parsed_docs: list[ParsedDocument] = field(default_factory=list)
    chunks: list[ChunkArtifact] = field(default_factory=list)
    embedding_model: str | None = None


class IngestionService:
    def __init__(
        self,
        *,
        job_store: InMemoryJobStore | None = None,
        embedding_client: EmbeddingClient | None = None,
        vector_store: BaseVectorStore | None = None,
        worker_count: int = 1,
        max_attempts: int = 5,
        retry_backoff_seconds: list[int] | None = None,
    ) -> None:
        self.job_store = job_store or InMemoryJobStore()
        self.embedding_client = embedding_client or ModelGatewayEmbeddingClient()
        self.vector_store = vector_store or self._default_vector_store()
        self.worker_count = worker_count
        self.max_attempts = max_attempts
        self.retry_backoff_seconds = retry_backoff_seconds or [60, 300, 900, 1800, 3600]

        self.queue: asyncio.Queue[str] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._retry_tasks: set[asyncio.Task] = set()
        self._artifacts: dict[str, JobArtifacts] = {}

    async def start(self) -> None:
        if self._workers:
            return
        for idx in range(self.worker_count):
            task = asyncio.create_task(self._worker_loop(worker_id=idx + 1), name=f"ingestion-worker-{idx+1}")
            self._workers.append(task)

    async def stop(self) -> None:
        for task in self._workers:
            task.cancel()
        for task in self._workers:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._workers.clear()

        for task in list(self._retry_tasks):
            task.cancel()
        for task in list(self._retry_tasks):
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._retry_tasks.clear()

        close_fn = getattr(self.vector_store, "close", None)
        if callable(close_fn):
            await close_fn()

    async def create_job(self, request: CreateIngestionJobRequest) -> CreateIngestionJobResponse:
        job_id = f"ing_{datetime.now(tz=timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        record = IngestionJobRecord(
            job_id=job_id,
            trace_id=request.trace_id,
            request_id=request.request_id,
            tenant_id=request.tenant_id,
            app_id=request.app_id,
            source=request.source,
            sync_mode=request.sync_mode,
            trigger=request.trigger,
            options=request.options,
            max_attempts=self.max_attempts,
        )
        await self.job_store.create(record)
        self._artifacts[job_id] = JobArtifacts()
        await self.queue.put(job_id)
        return CreateIngestionJobResponse(job_id=job_id, status=JobStatus.PENDING, created_at=record.created_at)

    async def get_job(self, job_id: str) -> IngestionJobStatusResponse:
        job = await self.job_store.get(job_id)
        return job.to_status_response()

    async def retry_job(self, job_id: str, request: RetryIngestionJobRequest) -> IngestionJobStatusResponse:
        job = await self.job_store.get(job_id)
        if job.status not in (JobStatus.FAILED, JobStatus.DEAD_LETTER):
            raise err_job_state_conflict(
                f"job '{job_id}' status is '{job.status}', only FAILED/DEAD_LETTER can be retried"
            )

        stage_name = self._resolve_retry_stage(job=job, from_stage=request.from_stage)
        await self.job_store.update(
            job_id,
            lambda j: self._prepare_retry(j, stage=stage_name),
        )
        await self.queue.put(job_id)
        job = await self.job_store.get(job_id)
        return job.to_status_response()

    def queue_size(self) -> int:
        return self.queue.qsize()

    async def _worker_loop(self, *, worker_id: int) -> None:
        while True:
            job_id = await self.queue.get()
            try:
                await self._run_job(job_id=job_id, worker_id=worker_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.exception("ingestion worker crashed job_id=%s worker_id=%s", job_id, worker_id)
            finally:
                self.queue.task_done()

    async def _run_job(self, *, job_id: str, worker_id: int) -> None:
        job = await self.job_store.get(job_id)
        if job.status == JobStatus.RUNNING:
            return

        await self.job_store.update(job_id, lambda j: self._mark_job_running(j))
        job = await self.job_store.get(job_id)
        artifacts = self._artifacts.setdefault(job_id, JobArtifacts())

        start_stage = job.retry_from_stage or StageName.PARSE
        stage_order = [StageName.PARSE, StageName.CHUNK, StageName.EMBED, StageName.INDEX]
        stage_index = stage_order.index(start_stage)
        active_stages = stage_order[stage_index:]

        try:
            for stage in active_stages:
                await self.job_store.update(job_id, lambda j, s=stage: self._mark_stage_running(j, s))
                if stage == StageName.PARSE:
                    artifacts.parsed_docs = self._parse_documents(job)
                    await self.job_store.update(
                        job_id,
                        lambda j: self._update_docs_stats(j, total=len(artifacts.parsed_docs), succeeded=len(artifacts.parsed_docs)),
                    )
                elif stage == StageName.CHUNK:
                    if not artifacts.parsed_docs:
                        artifacts.parsed_docs = self._parse_documents(job)
                    artifacts.chunks = self._chunk_documents(artifacts.parsed_docs)
                    await self.job_store.update(job_id, lambda j: self._update_chunks_total(j, total=len(artifacts.chunks)))
                elif stage == StageName.EMBED:
                    if not artifacts.chunks:
                        if not artifacts.parsed_docs:
                            artifacts.parsed_docs = self._parse_documents(job)
                        artifacts.chunks = self._chunk_documents(artifacts.parsed_docs)
                    artifacts.embedding_model = await self._embed_chunks(job=job, chunks=artifacts.chunks)
                    await self.job_store.update(
                        job_id,
                        lambda j: self._update_chunks_embedded(j, total=len(artifacts.chunks)),
                    )
                elif stage == StageName.INDEX:
                    if not artifacts.chunks:
                        raise err_invalid_params("index stage requires embedded chunks")
                    points_upserted = await self._index_chunks(
                        job=job,
                        chunks=artifacts.chunks,
                        embedding_model=artifacts.embedding_model,
                    )
                    await self.job_store.update(job_id, lambda j: self._update_points_upserted(j, total=points_upserted))

                await self.job_store.update(job_id, lambda j, s=stage: self._mark_stage_succeeded(j, s))

            await self.job_store.update(job_id, self._mark_job_succeeded)
            LOGGER.info("ingestion job succeeded job_id=%s worker_id=%s", job_id, worker_id)
        except IngestionError as exc:
            LOGGER.warning(
                "ingestion job failed job_id=%s worker_id=%s code=%s retryable=%s msg=%s",
                job_id,
                worker_id,
                exc.code,
                exc.retryable,
                exc.message,
            )
            await self._handle_job_failure(job_id=job_id, error=exc)
        except Exception as exc:
            wrapped = err_parse_failed(str(exc), retryable=False)
            await self._handle_job_failure(job_id=job_id, error=wrapped)

    async def _handle_job_failure(self, *, job_id: str, error: IngestionError) -> None:
        job = await self.job_store.get(job_id)
        attempts_exhausted = job.attempt >= job.max_attempts

        if attempts_exhausted:
            exhausted = err_retry_exhausted()
            await self.job_store.update(job_id, lambda j: self._mark_job_dead_letter(j, exhausted))
            return

        await self.job_store.update(job_id, lambda j: self._mark_job_failed(j, error))
        if error.retryable:
            delay = self.retry_backoff_seconds[min(job.attempt - 1, len(self.retry_backoff_seconds) - 1)]
            task = asyncio.create_task(self._requeue_after(job_id=job_id, delay_seconds=delay))
            self._retry_tasks.add(task)
            task.add_done_callback(lambda t: self._retry_tasks.discard(t))

    async def _requeue_after(self, *, job_id: str, delay_seconds: int) -> None:
        await asyncio.sleep(delay_seconds)
        job = await self.job_store.get(job_id)
        if job.status != JobStatus.FAILED:
            return
        await self.queue.put(job_id)

    def _parse_documents(self, job: IngestionJobRecord) -> list[ParsedDocument]:
        docs = job.options.inline_documents
        if not docs:
            synthetic = ParsedDocument(
                doc_id=f"doc_{job.source.source_id}_001",
                title=job.source.source_id,
                file_name=f"{job.source.source_id}.txt",
                file_type="text",
                content=f"synthetic content for source {job.source.source_id}",
                version_hash=self._version_hash(f"{job.source.source_id}|synthetic"),
            )
            return [synthetic]

        parsed_docs: list[ParsedDocument] = []
        for idx, doc in enumerate(docs):
            content = doc.content.strip()
            if not content:
                raise err_parse_failed(f"inline document '{doc.file_name}' content is empty", retryable=False)
            doc_id = doc.doc_id or f"doc_{idx+1:04d}"
            parsed_docs.append(
                ParsedDocument(
                    doc_id=doc_id,
                    title=doc.title or doc.file_name,
                    file_name=doc.file_name,
                    file_type=doc.file_type,
                    content=content,
                    version_hash=self._version_hash(f"{doc_id}|{doc.file_name}|{content}"),
                )
            )
        return parsed_docs

    def _chunk_documents(self, parsed_docs: list[ParsedDocument]) -> list[ChunkArtifact]:
        chunks: list[ChunkArtifact] = []
        window = 500
        overlap = 50
        for doc in parsed_docs:
            text = doc.content
            if not text:
                continue
            cursor = 0
            order = 0
            while cursor < len(text):
                end = min(cursor + window, len(text))
                chunk_text = text[cursor:end]
                chunk_id = f"{doc.doc_id}:{doc.version_hash[:8]}:{order:04d}"
                chunks.append(
                    ChunkArtifact(
                        chunk_id=chunk_id,
                        doc_id=doc.doc_id,
                        order=order,
                        text=chunk_text,
                        token_count=max(1, math.ceil(len(chunk_text) / 4)),
                        file_name=doc.file_name,
                        file_type=doc.file_type,
                        chunk_source_ref=self._build_chunk_source_ref(file_type=doc.file_type, order=order),
                        version_hash=doc.version_hash,
                    )
                )
                order += 1
                if end == len(text):
                    break
                cursor = max(end - overlap, cursor + 1)
        if not chunks:
            raise err_chunk_failed("no chunk generated", retryable=False)
        return chunks

    async def _embed_chunks(self, *, job: IngestionJobRecord, chunks: list[ChunkArtifact]) -> str:
        batch_size = 16
        used_model = job.options.preferred_embedding_model or "default"
        for i in range(0, len(chunks), batch_size):
            batch = chunks[i : i + batch_size]
            texts = [chunk.text for chunk in batch]
            vectors, model_used = await self.embedding_client.embed(
                trace_id=job.trace_id,
                request_id=job.request_id,
                app_id=job.app_id,
                texts=texts,
                preferred_model=job.options.preferred_embedding_model,
            )
            used_model = model_used or used_model
            if len(vectors) != len(batch):
                raise err_embedding_failed(
                    f"embedding vector count mismatch expected={len(batch)} actual={len(vectors)}",
                    retryable=False,
                )
            for chunk, vector in zip(batch, vectors):
                chunk.vector = [float(v) for v in vector]
        return used_model

    async def _index_chunks(
        self,
        *,
        job: IngestionJobRecord,
        chunks: list[ChunkArtifact],
        embedding_model: str | None,
    ) -> int:
        if not chunks or not chunks[0].vector:
            raise err_invalid_params("index stage requires embedded vectors")
        vector_size = len(chunks[0].vector or [])
        collection = build_collection_name(
            tenant_id=job.tenant_id,
            app_id=job.app_id,
            embedding_model=embedding_model or job.options.preferred_embedding_model or "default",
        )
        points = [
            VectorPoint(
                id=chunk.chunk_id,
                vector=chunk.vector or [],
                payload=PointPayload(
                    ingest_job_id=job.job_id,
                    tenant_id=job.tenant_id,
                    app_id=job.app_id,
                    source_id=job.source.source_id,
                    source_type=job.source.source_type,
                    doc_id=chunk.doc_id,
                    file_name=chunk.file_name,
                    file_type=chunk.file_type,
                    version_hash=chunk.version_hash,
                    chunk_source_ref=chunk.chunk_source_ref,
                    chunk_order=chunk.order,
                ),
            )
            for chunk in chunks
        ]
        return await self.vector_store.upsert_points(collection=collection, points=points, vector_size=vector_size)

    @staticmethod
    def _version_hash(raw: str) -> str:
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _build_chunk_source_ref(*, file_type: str, order: int) -> str:
        ft = file_type.lower()
        if ft == "pdf":
            return f"pdf:p{order + 1}"
        if ft == "word":
            return f"word:para{order + 1}"
        if ft == "excel":
            return f"excel:Sheet1!R{order + 1}"
        return f"text:L{order + 1}"

    @staticmethod
    def _default_vector_store() -> BaseVectorStore:
        qdrant_url = os.getenv("QDRANT_URL")
        if qdrant_url:
            api_key = os.getenv("QDRANT_API_KEY")
            return QdrantVectorStore(base_url=qdrant_url, api_key=api_key)
        return InMemoryVectorStore()

    @staticmethod
    def _resolve_retry_stage(*, job: IngestionJobRecord, from_stage: str) -> StageName:
        if from_stage != "auto":
            return StageName(from_stage)
        for stage in (StageName.PARSE, StageName.CHUNK, StageName.EMBED, StageName.INDEX):
            if job.stages[stage.value] == StageStatus.FAILED:
                return stage
        return StageName.PARSE

    @staticmethod
    def _prepare_retry(job: IngestionJobRecord, *, stage: StageName) -> None:
        job.retry_from_stage = stage
        job.error_code = None
        job.error_message = None
        if job.status == JobStatus.DEAD_LETTER:
            job.status = JobStatus.FAILED

    @staticmethod
    def _mark_job_running(job: IngestionJobRecord) -> None:
        job.status = JobStatus.RUNNING
        job.attempt += 1
        job.error_code = None
        job.error_message = None
        job.finished_at = None

    @staticmethod
    def _mark_stage_running(job: IngestionJobRecord, stage: StageName) -> None:
        job.current_stage = stage
        job.stages[stage.value] = StageStatus.RUNNING

    @staticmethod
    def _mark_stage_succeeded(job: IngestionJobRecord, stage: StageName) -> None:
        job.stages[stage.value] = StageStatus.SUCCEEDED

    @staticmethod
    def _mark_job_succeeded(job: IngestionJobRecord) -> None:
        job.status = JobStatus.SUCCEEDED
        job.current_stage = None
        job.finished_at = datetime.now(tz=timezone.utc)
        job.retry_from_stage = None

    @staticmethod
    def _mark_job_failed(job: IngestionJobRecord, error: IngestionError) -> None:
        job.status = JobStatus.FAILED
        if job.current_stage is not None:
            job.stages[job.current_stage.value] = StageStatus.FAILED
        job.error_code = error.code
        job.error_message = error.message
        job.finished_at = datetime.now(tz=timezone.utc)
        job.retry_from_stage = None

    @staticmethod
    def _mark_job_dead_letter(job: IngestionJobRecord, error: IngestionError) -> None:
        job.status = JobStatus.DEAD_LETTER
        if job.current_stage is not None:
            job.stages[job.current_stage.value] = StageStatus.FAILED
        job.error_code = error.code
        job.error_message = error.message
        job.finished_at = datetime.now(tz=timezone.utc)
        job.retry_from_stage = None

    @staticmethod
    def _update_docs_stats(job: IngestionJobRecord, *, total: int, succeeded: int) -> None:
        job.stats.docs_total = total
        job.stats.docs_succeeded = succeeded
        job.stats.docs_failed = max(total - succeeded, 0)

    @staticmethod
    def _update_chunks_total(job: IngestionJobRecord, *, total: int) -> None:
        job.stats.chunks_total = total

    @staticmethod
    def _update_chunks_embedded(job: IngestionJobRecord, *, total: int) -> None:
        job.stats.chunks_embedded = total

    @staticmethod
    def _update_points_upserted(job: IngestionJobRecord, *, total: int) -> None:
        job.stats.points_upserted = total
