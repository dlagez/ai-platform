# ingestion (v0.1)

Implementation follows `doc/v0.1/prd-v0.1-2-1ingestion.md`.

## Run API

```powershell
python -m uvicorn ingestion.api:app --port 8082 --host 0.0.0.0 --app-dir apps\platform-service\modules\ingestion
```

## Endpoints

1. `POST /api/v0.1/ingestion/jobs`
2. `GET /api/v0.1/ingestion/jobs/{job_id}`
3. `POST /api/v0.1/ingestion/jobs/{job_id}/retry`
4. `GET /api/v0.1/healthz`
5. `GET /api/v0.1/readyz`

## Config

1. `MODEL_GATEWAY_CONFIG` (optional): path of model-gateway yaml
2. `INGESTION_CONFIG` (optional): path of ingestion yaml (default: `configs/env/ingestion.yaml`)
3. `QDRANT_URL` (optional): remote Qdrant endpoint (`http://host:6333`)
4. `QDRANT_API_KEY` (optional): remote Qdrant api key
5. `QDRANT_PATH` (optional): local embedded Qdrant data directory (supports relative path)

`configs/env/ingestion.yaml` default:

```yaml
vector_store:
  qdrant_url: ""
  qdrant_api_key: ""
  qdrant_path: "index/qdrant_local"
```

`qdrant_path` relative paths are resolved against repo root.

`configs/env/model-gateway.yaml` now includes an `ingestion.embedding` route to local `bge-m3`
via provider `local_emb` (`http://127.0.0.1:8002/v1`).

Vector store selection order:

1. `QDRANT_URL` set (env or config) -> remote Qdrant HTTP mode
2. otherwise use local embedded Qdrant path (`QDRANT_PATH` env -> config `qdrant_path` -> default `index/qdrant_local`)

## Folder Ingestion Script

Use the helper script to scan a local folder and submit all matching files as `inline_documents`:

```bash
python scripts/ingestion/ingest_folder.py \
  --folder /home/zp/data/docs \
  --ingestion-url http://127.0.0.1:8082 \
  --tenant-id tenant-a \
  --app-id app-a \
  --preferred-embedding-model bge-m3
```

Default included extensions: `.txt,.md,.py,.json,.csv,.pdf,.docx` (override with `--allowed-exts`).

`ingest_folder.py` extracts text from `.pdf` and `.docx` before upload.
If you are using a fresh venv, run `pip install -r requirements.txt` first.
