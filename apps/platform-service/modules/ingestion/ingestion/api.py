from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException

from .errors import IngestionError
from .schemas import (
    CreateIngestionJobRequest,
    CreateIngestionJobResponse,
    IngestionJobStatusResponse,
    RetryIngestionJobRequest,
)
from .service import IngestionService


logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger("ingestion_api")


class _ServiceHolder:
    service: IngestionService | None = None

    @classmethod
    async def get_service(cls) -> IngestionService:
        if cls.service is None:
            cls.service = IngestionService()
            await cls.service.start()
            LOGGER.info("ingestion service initialized")
        return cls.service

    @classmethod
    async def stop_service(cls) -> None:
        if cls.service is not None:
            await cls.service.stop()
            cls.service = None


app = FastAPI(title="Ingestion", version="0.1.0")


@app.on_event("startup")
async def startup() -> None:
    await _ServiceHolder.get_service()


@app.on_event("shutdown")
async def shutdown() -> None:
    await _ServiceHolder.stop_service()


@app.get("/api/v0.1/healthz")
async def healthz() -> dict:
    service = await _ServiceHolder.get_service()
    return {
        "status": "ok",
        "queue_size": service.queue_size(),
    }


@app.get("/api/v0.1/readyz")
async def readyz() -> dict:
    service = await _ServiceHolder.get_service()
    return {
        "status": "ready",
        "queue_size": service.queue_size(),
    }


@app.post("/api/v0.1/ingestion/jobs", response_model=CreateIngestionJobResponse)
async def create_job(payload: CreateIngestionJobRequest) -> CreateIngestionJobResponse:
    try:
        service = await _ServiceHolder.get_service()
        return await service.create_job(payload)
    except IngestionError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message}) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"create job failed: {exc}") from exc


@app.get("/api/v0.1/ingestion/jobs/{job_id}", response_model=IngestionJobStatusResponse)
async def get_job(job_id: str) -> IngestionJobStatusResponse:
    try:
        service = await _ServiceHolder.get_service()
        return await service.get_job(job_id)
    except IngestionError as exc:
        raise HTTPException(status_code=404, detail={"code": exc.code, "message": exc.message}) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"query job failed: {exc}") from exc


@app.post("/api/v0.1/ingestion/jobs/{job_id}/retry", response_model=IngestionJobStatusResponse)
async def retry_job(job_id: str, payload: RetryIngestionJobRequest) -> IngestionJobStatusResponse:
    try:
        service = await _ServiceHolder.get_service()
        return await service.retry_job(job_id, payload)
    except IngestionError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message}) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"retry job failed: {exc}") from exc


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("ingestion.api:app", host="0.0.0.0", port=8082, reload=False)

