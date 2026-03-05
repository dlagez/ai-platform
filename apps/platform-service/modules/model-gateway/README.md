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

Provider supports explicit `adapter`:

1. `openai_compatible` (for OpenAI-compatible endpoints, including local vLLM embedding)
2. `anthropic`

If `adapter` is missing, only legacy provider names are inferred:

1. `openai` -> `openai_compatible`
2. `anthropic` -> `anthropic`

Override:

```powershell
$env:MODEL_GATEWAY_CONFIG="D:\\code3\\ai-platform\\configs\\env\\model-gateway.yaml"
```
