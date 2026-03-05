from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class JobStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    DEAD_LETTER = "DEAD_LETTER"


class StageName(str, Enum):
    PARSE = "parse"
    CHUNK = "chunk"
    EMBED = "embed"
    INDEX = "index"


class StageStatus(str, Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class SourceInfo(BaseModel):
    source_id: str
    source_type: str
    connector_config_ref: str | None = None


class InlineDocument(BaseModel):
    doc_id: str | None = None
    title: str | None = None
    file_name: str
    file_type: str = "text"
    content: str


class IngestionOptions(BaseModel):
    force_reindex: bool = False
    chunk_policy: str = "default"
    preferred_embedding_model: str | None = None
    inline_documents: list[InlineDocument] = Field(default_factory=list)


class CreateIngestionJobRequest(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    trace_id: str
    request_id: str
    tenant_id: str
    app_id: str
    source: SourceInfo
    sync_mode: Literal["incremental", "full"] = "incremental"
    trigger: Literal["manual", "schedule", "event"] = "manual"
    options: IngestionOptions = Field(default_factory=IngestionOptions)


class CreateIngestionJobResponse(BaseModel):
    ok: bool = True
    job_id: str
    status: JobStatus
    created_at: datetime


class RetryIngestionJobRequest(BaseModel):
    reason: str = "manual retry"
    from_stage: Literal["parse", "chunk", "embed", "index", "auto"] = "auto"


class JobStats(BaseModel):
    docs_total: int = 0
    docs_succeeded: int = 0
    docs_failed: int = 0
    chunks_total: int = 0
    chunks_embedded: int = 0
    points_upserted: int = 0


class IngestionJobStatusResponse(BaseModel):
    ok: bool = True
    job_id: str
    status: JobStatus
    current_stage: StageName | None = None
    attempt: int = 0
    max_attempts: int = 5
    stages: dict[str, StageStatus]
    stats: JobStats
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime
    finished_at: datetime | None = None


class IngestionJobRecord(BaseModel):
    job_id: str
    trace_id: str
    request_id: str
    tenant_id: str
    app_id: str
    source: SourceInfo
    sync_mode: Literal["incremental", "full"] = "incremental"
    trigger: Literal["manual", "schedule", "event"] = "manual"
    options: IngestionOptions = Field(default_factory=IngestionOptions)

    status: JobStatus = JobStatus.PENDING
    current_stage: StageName | None = None
    attempt: int = 0
    max_attempts: int = 5
    stages: dict[str, StageStatus] = Field(
        default_factory=lambda: {
            StageName.PARSE.value: StageStatus.PENDING,
            StageName.CHUNK.value: StageStatus.PENDING,
            StageName.EMBED.value: StageStatus.PENDING,
            StageName.INDEX.value: StageStatus.PENDING,
        }
    )
    stats: JobStats = Field(default_factory=JobStats)
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(tz=timezone.utc))
    finished_at: datetime | None = None
    retry_from_stage: StageName | None = None

    def to_status_response(self) -> IngestionJobStatusResponse:
        return IngestionJobStatusResponse(
            job_id=self.job_id,
            status=self.status,
            current_stage=self.current_stage,
            attempt=self.attempt,
            max_attempts=self.max_attempts,
            stages=self.stages,
            stats=self.stats,
            error_code=self.error_code,
            error_message=self.error_message,
            created_at=self.created_at,
            updated_at=self.updated_at,
            finished_at=self.finished_at,
        )


class ParsedDocument(BaseModel):
    doc_id: str
    title: str
    file_name: str
    file_type: str
    content: str
    version_hash: str


class ChunkArtifact(BaseModel):
    chunk_id: str
    doc_id: str
    order: int
    text: str
    token_count: int
    file_name: str
    file_type: str
    chunk_source_ref: str
    version_hash: str
    vector: list[float] | None = None


class PointPayload(BaseModel):
    ingest_job_id: str
    tenant_id: str
    app_id: str
    source_id: str
    source_type: str
    doc_id: str
    file_name: str
    file_type: str
    version_hash: str
    acl_version: str = "acl_v1"
    chunk_source_ref: str
    deleted_at: str | None = None
    chunk_order: int


class VectorPoint(BaseModel):
    id: str
    vector: list[float]
    payload: PointPayload

