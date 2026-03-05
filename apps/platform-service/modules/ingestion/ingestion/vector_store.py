from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod

import httpx

from .errors import err_point_upsert_failed
from .schemas import VectorPoint


LOGGER = logging.getLogger("ingestion_vector_store")
SAFE_NAME = re.compile(r"[^a-z0-9_]+")


def build_collection_name(tenant_id: str, app_id: str, embedding_model: str) -> str:
    name = f"col_{tenant_id}_{app_id}_{embedding_model}".lower()
    name = SAFE_NAME.sub("_", name).strip("_")
    return name[:200] or "col_default"


class BaseVectorStore(ABC):
    @abstractmethod
    async def upsert_points(
        self,
        *,
        collection: str,
        points: list[VectorPoint],
        vector_size: int,
    ) -> int:
        raise NotImplementedError


class InMemoryVectorStore(BaseVectorStore):
    def __init__(self) -> None:
        self._collections: dict[str, dict[str, VectorPoint]] = {}

    async def upsert_points(
        self,
        *,
        collection: str,
        points: list[VectorPoint],
        vector_size: int,
    ) -> int:
        bucket = self._collections.setdefault(collection, {})
        for point in points:
            if len(point.vector) != vector_size:
                raise err_point_upsert_failed(
                    f"vector size mismatch for point '{point.id}': expected={vector_size}, actual={len(point.vector)}",
                    retryable=False,
                )
            bucket[point.id] = point
        return len(points)


class QdrantVectorStore(BaseVectorStore):
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        headers = {"api-key": api_key} if api_key else {}
        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout_seconds, headers=headers)
        self._collection_ready: set[str] = set()

    async def close(self) -> None:
        await self.client.aclose()

    async def upsert_points(
        self,
        *,
        collection: str,
        points: list[VectorPoint],
        vector_size: int,
    ) -> int:
        try:
            await self._ensure_collection(collection=collection, vector_size=vector_size)
            payload = {
                "points": [
                    {
                        "id": point.id,
                        "vector": point.vector,
                        "payload": point.payload.model_dump(mode="json"),
                    }
                    for point in points
                ]
            }
            resp = await self.client.put(f"/collections/{collection}/points", json=payload)
            if resp.status_code >= 300:
                raise err_point_upsert_failed(
                    f"qdrant upsert failed status={resp.status_code} body={resp.text}",
                    retryable=True,
                )
            return len(points)
        except Exception as exc:
            if isinstance(exc, Exception) and hasattr(exc, "code"):
                raise
            raise err_point_upsert_failed(f"qdrant upsert failed: {exc}", retryable=True) from exc

    async def _ensure_collection(self, *, collection: str, vector_size: int) -> None:
        if collection in self._collection_ready:
            return

        body = {
            "vectors": {
                "size": vector_size,
                "distance": "Cosine",
            }
        }
        resp = await self.client.put(f"/collections/{collection}", json=body)
        if resp.status_code >= 300:
            raise err_point_upsert_failed(
                f"qdrant ensure collection failed status={resp.status_code} body={resp.text}",
                retryable=True,
            )

        await self._ensure_payload_indexes(collection=collection)
        self._collection_ready.add(collection)

    async def _ensure_payload_indexes(self, *, collection: str) -> None:
        for field_name, field_schema in (
            ("source_id", "keyword"),
            ("source_type", "keyword"),
            ("doc_id", "keyword"),
            ("file_name", "keyword"),
            ("deleted_at", "datetime"),
        ):
            body = {"field_name": field_name, "field_schema": field_schema}
            resp = await self.client.put(f"/collections/{collection}/index", json=body)
            if resp.status_code >= 300:
                LOGGER.warning(
                    "qdrant payload index ensure failed collection=%s field=%s status=%s",
                    collection,
                    field_name,
                    resp.status_code,
                )

