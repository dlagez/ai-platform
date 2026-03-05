from __future__ import annotations

import asyncio
import logging
from pathlib import Path
import re
import uuid
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


class LocalQdrantVectorStore(BaseVectorStore):
    def __init__(
        self,
        *,
        path: str,
    ) -> None:
        try:
            from qdrant_client import QdrantClient
            from qdrant_client.http import models as qmodels
        except ImportError as exc:
            raise RuntimeError(
                "qdrant-client is required for local qdrant mode; install dependency 'qdrant-client'"
            ) from exc

        self.path = str(Path(path))
        Path(self.path).mkdir(parents=True, exist_ok=True)
        self._qmodels = qmodels
        self.client = QdrantClient(path=self.path)
        self._collection_ready: set[str] = set()

    async def close(self) -> None:
        close_fn = getattr(self.client, "close", None)
        if callable(close_fn):
            await asyncio.to_thread(close_fn)

    async def upsert_points(
        self,
        *,
        collection: str,
        points: list[VectorPoint],
        vector_size: int,
    ) -> int:
        try:
            await self._ensure_collection(collection=collection, vector_size=vector_size)
            qpoints = []
            for point in points:
                if len(point.vector) != vector_size:
                    raise err_point_upsert_failed(
                        f"vector size mismatch for point '{point.id}': expected={vector_size}, actual={len(point.vector)}",
                        retryable=False,
                    )
                qpoints.append(
                    self._qmodels.PointStruct(
                        id=self._normalize_point_id(point.id),
                        vector=point.vector,
                        payload=point.payload.model_dump(mode="json"),
                    )
                )
            await asyncio.to_thread(
                self.client.upsert,
                collection_name=collection,
                points=qpoints,
                wait=True,
            )
            return len(points)
        except Exception as exc:
            if isinstance(exc, Exception) and hasattr(exc, "code"):
                raise
            raise err_point_upsert_failed(f"local qdrant upsert failed: {exc}", retryable=True) from exc

    async def _ensure_collection(self, *, collection: str, vector_size: int) -> None:
        if collection in self._collection_ready:
            return

        exists = await asyncio.to_thread(self.client.collection_exists, collection_name=collection)
        if not exists:
            await asyncio.to_thread(
                self.client.create_collection,
                collection_name=collection,
                vectors_config=self._qmodels.VectorParams(
                    size=vector_size,
                    distance=self._qmodels.Distance.COSINE,
                ),
            )

        await self._ensure_payload_indexes(collection=collection)
        self._collection_ready.add(collection)

    async def _ensure_payload_indexes(self, *, collection: str) -> None:
        for field_name, field_schema in (
            ("source_id", self._qmodels.PayloadSchemaType.KEYWORD),
            ("source_type", self._qmodels.PayloadSchemaType.KEYWORD),
            ("doc_id", self._qmodels.PayloadSchemaType.KEYWORD),
            ("file_name", self._qmodels.PayloadSchemaType.KEYWORD),
            ("deleted_at", self._qmodels.PayloadSchemaType.DATETIME),
        ):
            try:
                await asyncio.to_thread(
                    self.client.create_payload_index,
                    collection_name=collection,
                    field_name=field_name,
                    field_schema=field_schema,
                    wait=True,
                )
            except Exception as exc:
                LOGGER.warning(
                    "local qdrant payload index ensure failed collection=%s field=%s err=%s",
                    collection,
                    field_name,
                    exc,
                )

    @staticmethod
    def _normalize_point_id(point_id: str | int) -> str | int:
        if isinstance(point_id, int):
            return point_id
        try:
            uuid.UUID(point_id)
            return point_id
        except ValueError:
            # Local Qdrant requires string IDs in UUID format.
            return str(uuid.uuid5(uuid.NAMESPACE_URL, f"ingestion:{point_id}"))
