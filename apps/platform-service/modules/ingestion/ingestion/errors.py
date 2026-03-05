from __future__ import annotations

from dataclasses import dataclass


@dataclass
class IngestionError(Exception):
    code: str
    message: str
    retryable: bool = False

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


def err_job_not_found() -> IngestionError:
    return IngestionError("IGT_001", "job not found")


def err_invalid_params(message: str) -> IngestionError:
    return IngestionError("IGT_002", message)


def err_job_state_conflict(message: str) -> IngestionError:
    return IngestionError("IGT_003", message)


def err_parse_failed(message: str, *, retryable: bool = False) -> IngestionError:
    return IngestionError("IGT_005", message, retryable=retryable)


def err_chunk_failed(message: str, *, retryable: bool = False) -> IngestionError:
    return IngestionError("IGT_006", message, retryable=retryable)


def err_embedding_failed(message: str, *, retryable: bool = True) -> IngestionError:
    return IngestionError("IGT_007", message, retryable=retryable)


def err_point_upsert_failed(message: str, *, retryable: bool = True) -> IngestionError:
    return IngestionError("IGT_008", message, retryable=retryable)


def err_retry_exhausted(message: str = "retry exhausted") -> IngestionError:
    return IngestionError("IGT_009", message, retryable=False)


def err_idempotency_conflict(message: str) -> IngestionError:
    return IngestionError("IGT_010", message, retryable=False)

