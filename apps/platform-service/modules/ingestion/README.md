# ingestion (v0.1)

Implementation follows `doc/v0.1/prd-v0.1-2-1ingestion.md`.

## Run API

```powershell
cd apps/platform-service/modules/ingestion
..\..\..\..\.venv\Scripts\python.exe -m uvicorn ingestion.api:app --port 8082
```

## Endpoints

1. `POST /api/v0.1/ingestion/jobs`
2. `GET /api/v0.1/ingestion/jobs/{job_id}`
3. `POST /api/v0.1/ingestion/jobs/{job_id}/retry`
4. `GET /api/v0.1/healthz`
5. `GET /api/v0.1/readyz`

## Config

1. `MODEL_GATEWAY_CONFIG` (optional): path of model-gateway yaml
2. `QDRANT_URL` (optional): enable Qdrant vector write
3. `QDRANT_API_KEY` (optional): Qdrant api key

When `QDRANT_URL` is missing, ingestion uses in-memory vector store.

