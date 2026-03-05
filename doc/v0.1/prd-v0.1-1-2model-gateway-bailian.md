# Model Gateway 阿里云百炼接入 PRD（v0.1.x）

- 文档版本：v0.1.x
- 文档状态：可执行草案
- 日期：2026-03-04
- 所属项目：AI 平台（单服务形态）
- 模块路径：`apps/platform-service/modules/model-gateway`

## 1. 当前支持结论（先回答问题）

结论：**当前代码对百炼是“部分支持”，不是“原生支持”。**

1. `model-gateway` 已有 `LangChainOpenAIAdapter`，支持 `base_url + api_key + model` 的 OpenAI 兼容调用。
2. 阿里云百炼提供 OpenAI-Compatible API，因此在技术上可复用该适配器。
3. 但当前 `ModelGateway.from_config_file` 仅在 provider 名称为 `openai` 或 `anthropic` 时注册适配器。
4. 这意味着：
   - 可以把 `openai` provider 的 `base_url/api_key` 改成百炼来“单独使用百炼”。
   - 不能在不改代码的前提下同时优雅配置 `openai` 与 `bailian` 两个 provider（`bailian` 不会自动挂载适配器）。

## 2. 背景与目标

为满足国内模型接入、成本和可用性需求，在 `model-gateway` 中正式接入阿里云百炼（Bailian / DashScope），并支持与 OpenAI、Anthropic 混合路由和 fallback。

目标：
1. 新增 `bailian` provider 的一等公民支持（generation + embedding）。
2. 保持现有配置向后兼容，不破坏 `openai/anthropic` 现网配置。
3. 支持同一环境下 `openai + bailian + anthropic` 并存路由。
4. 复用现有错误码、重试、熔断和追踪机制，不新增调用链复杂度。

## 3. 范围定义

### 3.1 In Scope（v0.1.x 必做）

1. `ProviderConfig` 扩展 `adapter` 字段，解耦“provider 名称”和“适配器类型”。
2. `from_config_file` 改为按 `provider.adapter` 动态注册适配器。
3. 接入 `bailian` provider，复用 OpenAI 兼容适配器。
4. 更新默认配置示例与文档，补齐单测。
5. 支持 generation 与 embedding 路由到百炼模型。

### 3.2 Out of Scope（v0.1.x 不做）

1. 实时从百炼控制台自动同步模型列表。
2. provider 级别的币种换算与多币种成本统一结算（先沿用 USD 字段约定）。
3. 流式输出（`stream=true`）能力增强（当前保持 v0.1 现状）。

## 4. 外部依赖与接入约束（官方文档）

1. 百炼 OpenAI-Compatible 端点（中国站）：
   - `https://dashscope.aliyuncs.com/compatible-mode/v1`
2. 百炼 OpenAI-Compatible 端点（国际站）：
   - `https://dashscope-intl.aliyuncs.com/compatible-mode/v1`
3. 鉴权：
   - 使用 DashScope API Key（建议环境变量注入）。
4. 模型：
   - 文本生成可用 Qwen 系列（如 `qwen-plus` / `qwen-max`，以控制台可用模型为准）。
   - 向量可用 `text-embedding-v4`（以控制台可用模型为准）。

## 5. 方案设计

### 5.1 配置模型变更

`ProviderConfig` 新增字段：

```python
adapter: Literal["openai_compatible", "anthropic"] | None = None
```

兼容策略：
1. 若 `adapter` 显式配置，则按配置值注册适配器。
2. 若未配置 `adapter`，沿用旧逻辑推断：
   - `provider_name == "openai"` -> `openai_compatible`
   - `provider_name == "anthropic"` -> `anthropic`
3. 其他 provider 且未配 `adapter`：配置校验失败（启动期 fail fast）。

### 5.2 适配器注册策略

将当前“按 provider 名称硬编码”改为“按 adapter 类型映射”：

1. `openai_compatible` -> `LangChainOpenAIAdapter`
2. `anthropic` -> `LangChainAnthropicAdapter`

设计收益：
1. `bailian` 可直接复用 `openai_compatible`。
2. 后续接入其它 OpenAI 兼容平台无需再改网关核心流程。

### 5.3 推荐配置示例

```yaml
providers:
  openai:
    adapter: openai_compatible
    base_url: ${OPENAI_BASE_URL}
    api_key: ${OPENAI_API_KEY}
    timeout_ms: 10000
  bailian:
    adapter: openai_compatible
    base_url: ${BAILIAN_BASE_URL}
    api_key: ${BAILIAN_API_KEY}
    timeout_ms: 10000
  anthropic:
    adapter: anthropic
    base_url: ${ANTHROPIC_BASE_URL}
    api_key: ${ANTHROPIC_API_KEY}
    timeout_ms: 10000

models:
  - id: gpt-4o-mini
    provider: openai
    task: generation
    enabled: true
    priority: 10
    input_price_per_1k: 0.00015
    output_price_per_1k: 0.0006
  - id: qwen-plus
    provider: bailian
    task: generation
    enabled: true
    priority: 15
    input_price_per_1k: 0.0
    output_price_per_1k: 0.0
  - id: text-embedding-v4
    provider: bailian
    task: embedding
    enabled: true
    priority: 10
    input_price_per_1k: 0.0
    output_price_per_1k: 0.0

routes:
  rag_qa:
    generation:
      - gpt-4o-mini
      - qwen-plus
    embedding: []
  ingestion:
    generation: []
    embedding:
      - text-embedding-v4
```

环境变量建议：
1. `BAILIAN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1`
2. `BAILIAN_API_KEY=<DashScope API Key>`

### 5.4 错误处理与观测

1. 沿用现有 `MGW_001 ~ MGW_008`，不新增错误码。
2. provider 配置错误统一落在 `MGW_002`。
3. 日志字段不变，新增 provider 名称 `bailian` 的可观测维度。

## 6. 研发改造清单（代码级）

1. `apps/platform-service/modules/model-gateway/model_gateway/config.py`
   - 扩展 `ProviderConfig` 增加 `adapter` 字段。
   - 在配置加载后增加 `adapter` 合法性校验（含向后兼容推断）。
2. `apps/platform-service/modules/model-gateway/model_gateway/gateway.py`
   - `from_config_file` 按 `provider_cfg.adapter` 注册适配器。
   - 对未知 adapter 启动失败并返回明确报错信息。
3. `configs/env/model-gateway.yaml`
   - 增加 `bailian` provider 示例和对应模型示例。
4. `tests/unit/model_gateway/test_gateway.py`
   - 新增 `bailian` provider 场景：生成、向量、fallback。
5. `doc/v0.1/*`
   - 补充百炼接入说明与运维配置说明。

## 7. 测试与验收

### 7.1 单元测试

1. `adapter` 显式配置可正确注册。
2. 旧配置（无 `adapter`）仍可跑通 `openai/anthropic`。
3. `bailian` provider 可执行 generation 与 embedding。
4. `openai -> bailian` 与 `bailian -> anthropic` fallback 可用。
5. 非法 adapter 配置在启动期失败。

### 7.2 联调测试（最小）

1. `POST /internal/model-gateway/generate` 使用 `qwen-plus` 返回 `ok=true`。
2. `POST /internal/model-gateway/embed` 使用 `text-embedding-v4` 返回向量。
3. 主模型故障注入后，fallback 到百炼模型成功。

### 7.3 DoD

1. 同一份配置中可同时存在 `openai`、`bailian`、`anthropic`。
2. 百炼生成与向量接口均可用。
3. 回归测试不破坏现有 v0.1 行为。
4. `healthz` 能看到 `bailian` provider 与对应模型。

## 8. 风险与缓解

1. 风险：百炼模型名更新导致配置失效。
   - 缓解：模型名以控制台为准，配置改为环境分层可热切换发布。
2. 风险：OpenAI 兼容细节差异导致 token 统计不一致。
   - 缓解：统计字段允许降级为 0；成本字段仅作为估算。
3. 风险：跨区域端点配置错误（中国站/国际站）。
   - 缓解：在部署文档明确区分 `dashscope` 与 `dashscope-intl`。

## 9. 里程碑（建议）

1. D1：完成配置结构与适配器注册重构。
2. D2：完成 `bailian` 路由联调与单测。
3. D3：补文档、回归、灰度上线。

## 10. 当天可用的临时接入方案（不改代码）

若需“今天先跑通百炼”，可直接复用现有 `openai` provider：

1. 将 `providers.openai.base_url` 配置为百炼端点。
2. 将 `providers.openai.api_key` 配置为 DashScope API Key。
3. `models[*].provider` 保持 `openai`，模型 ID 改为百炼可用模型（如 `qwen-plus`、`text-embedding-v4`）。

限制：
1. 该方案会占用 `openai` provider 名称。
2. 无法与真实 OpenAI provider 并存。
3. 不满足长期治理与多 provider 运维要求，仅建议短期验证。

## 11. 参考资料

1. 阿里云百炼 OpenAI 兼容文档（文本生成）：
   - https://help.aliyun.com/zh/model-studio/compatibility-of-openai-with-dashscope
2. 阿里云百炼 OpenAI 兼容文档（文本向量）：
   - https://www.alibabacloud.com/help/en/model-studio/text-embedding-synchronous-api-openai
3. 百炼 API Key 获取：
   - https://www.alibabacloud.com/help/en/model-studio/get-api-key
4. 百炼模型列表总览：
   - https://www.alibabacloud.com/help/en/model-studio/getting-started/models
