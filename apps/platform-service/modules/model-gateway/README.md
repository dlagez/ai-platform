# model-gateway (v0.1)

Implementation follows `doc/v0.1/prd-v0.1-model-gateway.md`.

## Run API

```powershell
cd apps/platform-service/modules/model-gateway
..\..\..\..\.venv\Scripts\python.exe -m uvicorn model_gateway.api:app --port 8081
```

## Config

Default config path:

`configs/env/model-gateway.yaml`

Override:

```powershell
$env:MODEL_GATEWAY_CONFIG="D:\\code3\\ai-platform\\configs\\env\\model-gateway.yaml"
```

