# Model Gateway PRD（v0.1）

- 文档版本：v0.1
- 文档状态：可执行草案
- 日期：2026-03-04
- 所属项目：AI 平台（单服务形态）
- 模块路径：`apps/platform-service/modules/model-gateway`

## 1. 背景与目标
`model-gateway` 是 `platform-service` 内的核心模块，负责把上游（`orchestration`、`ingestion`）的模型调用请求统一路由到具体模型提供方，并处理失败降级。

v0.1 目标：
1. 统一模型调用入口，屏蔽不同 Provider SDK 差异。
2. 支持至少 2 组模型配置，并实现可控 fallback。
3. 提供超时、重试、熔断基础能力，避免单模型故障拖垮主链路。
4. 返回统一响应结构，便于上游编排与后续评测扩展。

## 2. 范围定义

### 2.1 In Scope（v0.1 必做）
1. 文本生成（generation）统一调用接口。
2. 向量生成（embedding）统一调用接口（供 ingestion 使用）。
3. 基于“场景 + 能力 + 优先级”的静态路由。
4. fallback 链路（主模型失败后按顺位切换）。
5. 超时控制、有限重试、基础熔断（单实例内存态）。
6. 统一错误码与响应格式。
7. 成本统计字段回传（token 与估算成本）。

### 2.2 Out of Scope（v0.1 不做）
1. 动态智能路由（实时按延迟/成本/质量打分自动学习）。
2. 多区域/多集群全局熔断协同。
3. 复杂配额中心与精细化计费结算。
4. 独立部署微服务（v0.1 仅模块化，不独立服务化）。

## 3. 模块职责与边界

上游调用方：
1. `orchestration`：调用生成接口完成问答/Agent 推理。
2. `ingestion`：调用 embedding 接口完成向量化。

下游依赖：
1. Provider Adapter（OpenAI/Anthropic/阿里云百炼等，按实际接入）。
2. 配置中心（v0.1 使用本地配置文件 + 环境变量）。

模块职责：
1. 请求标准化（normalize）。
2. 路由选择（route select）。
3. 执行与 fallback（invoke + fallback）。
4. 响应标准化（normalize response）。
5. 失败分类（retryable / fallbackable / fatal）。

## 4. 逻辑架构

```text
orchestration / ingestion
        |
        v
model-gateway facade
  ├─ Request Normalizer
  ├─ Route Engine
  ├─ Invocation Engine
  │    ├─ Timeout Controller
  │    ├─ Retry Controller
  │    └─ Circuit Breaker
  ├─ Provider Adapters
  └─ Response & Error Normalizer
```

说明：v0.1 不拆微服务，`model-gateway` 作为模块直接被同进程调用。

## 5. 接口契约（模块内）

### 5.1 Generation 请求对象

```json
{
  "trace_id": "string",
  "request_id": "string",
  "app_id": "string",
  "scene": "rag_qa|chat|agent",
  "messages": [{"role":"system|user|assistant","content":"string"}],
  "params": {
    "temperature": 0.2,
    "top_p": 0.9,
    "max_tokens": 1024,
    "stream": false
  },
  "routing": {
    "preferred_model": "optional-string",
    "allow_fallback": true,
    "max_fallback_hops": 2
  },
  "deadline_ms": 12000
}
```

### 5.2 Embedding 请求对象

```json
{
  "trace_id": "string",
  "request_id": "string",
  "app_id": "string",
  "scene": "ingestion",
  "texts": ["string"],
  "routing": {
    "preferred_model": "optional-string",
    "allow_fallback": true,
    "max_fallback_hops": 1
  },
  "deadline_ms": 15000
}
```

### 5.3 统一响应对象

```json
{
  "ok": true,
  "provider": "openai",
  "model": "gpt-4o-mini",
  "attempts": 1,
  "fallback_used": false,
  "usage": {
    "prompt_tokens": 100,
    "completion_tokens": 200,
    "total_tokens": 300,
    "estimated_cost": 0.0012,
    "currency": "USD"
  },
  "latency_ms": 820,
  "data": {}
}
```

失败时：

```json
{
  "ok": false,
  "error": {
    "code": "MGW_004",
    "message": "all fallback models failed",
    "retryable": true,
    "provider_error": "..."
  },
  "attempts": 3,
  "fallback_used": true
}
```

## 6. 模型配置设计

配置文件建议：`configs/env/model-gateway.yaml`

```yaml
providers:
  openai:
    base_url: ${OPENAI_BASE_URL}
    api_key: ${OPENAI_API_KEY}
    timeout_ms: 10000
  anthropic:
    base_url: ${ANTHROPIC_BASE_URL}
    api_key: ${ANTHROPIC_API_KEY}
    timeout_ms: 10000

models:
  - id: gpt-4o-mini
    provider: openai
    task: generation
    enabled: true
    priority: 10
    max_context_tokens: 128000
    input_price_per_1k: 0.00015
    output_price_per_1k: 0.0006
  - id: claude-3-5-haiku
    provider: anthropic
    task: generation
    enabled: true
    priority: 20
    max_context_tokens: 200000
    input_price_per_1k: 0.00025
    output_price_per_1k: 0.00125
  - id: text-embedding-3-large
    provider: openai
    task: embedding
    enabled: true
    priority: 10

routes:
  rag_qa:
    generation: [gpt-4o-mini, claude-3-5-haiku]
  chat:
    generation: [gpt-4o-mini, claude-3-5-haiku]
  ingestion:
    embedding: [text-embedding-3-large]
```

约束：
1. `routes` 中所有模型必须存在于 `models` 且 `enabled=true`。
2. 若 `preferred_model` 存在且可用，优先使用。
3. 若目标模型不支持请求能力（如不支持 stream），进入 fallback。

## 7. 路由与执行逻辑

### 7.1 路由规则（v0.1）
1. 先按任务类型过滤：`generation` 或 `embedding`。
2. 再按场景获取候选列表：`routes[scene][task]`。
3. 若传入 `preferred_model` 且合法，插入候选首位。
4. 过滤掉熔断打开（OPEN）的模型。
5. 依次执行候选，直到成功或候选耗尽。

### 7.2 执行状态机

```text
RECEIVED
  -> ROUTED
  -> INVOKING_PRIMARY
     -> SUCCESS
     -> FAILED_RETRYABLE -> RETRYING -> SUCCESS/FAILED
     -> FAILED_FALLBACKABLE -> INVOKING_FALLBACK ...
     -> FAILED_FATAL
  -> DONE
```

### 7.3 fallback 触发条件
触发 fallback：
1. 网络错误（连接失败、DNS、连接重置）。
2. 下游超时。
3. Provider `429`（限流）或 `5xx`。
4. 明确的 provider-unavailable 错误。

不触发 fallback：
1. 参数非法（4xx 业务参数错误）。
2. 上游请求已超时（deadline 已耗尽）。
3. 显式 `allow_fallback=false`。

### 7.4 重试策略
1. 每个模型最多重试 1 次（仅 retryable 错误）。
2. 重试退避：固定 200ms（v0.1 简化策略）。
3. 若重试后仍失败，再进入下一模型 fallback。

### 7.5 超时策略
1. 每次请求有 `deadline_ms`（默认 generation=12000ms, embedding=15000ms）。
2. 单次模型调用超时：`min(provider.timeout_ms, 剩余deadline - 500ms)`。
3. 若剩余 deadline <= 500ms，直接返回 `MGW_003`（deadline exceeded）。

## 8. 熔断设计（v0.1 基础版）

熔断维度：`provider + model`

状态：`CLOSED`、`OPEN`、`HALF_OPEN`

规则：
1. 在 60s 滑窗内，请求数 >= 20 且失败率 >= 50% 时，进入 `OPEN`。
2. `OPEN` 持续 30s，不再分配流量。
3. 30s 后进入 `HALF_OPEN`，放行 5 个探测请求。
4. 探测成功率 >= 80% 则回 `CLOSED`，否则回 `OPEN`。

实现方式：
1. v0.1 使用进程内内存状态。
2. 重启后熔断状态丢失（可接受，后续版本升级为 Redis 共享状态）。

## 9. 错误码规范

1. `MGW_001`：no route found（无可用路由）。
2. `MGW_002`：provider auth/config invalid（配置或凭证错误）。
3. `MGW_003`：request deadline exceeded（调用超时）。
4. `MGW_004`：all fallback models failed（主备全失败）。
5. `MGW_005`：provider rate limited（下游限流）。
6. `MGW_006`：invalid request params（请求参数非法）。
7. `MGW_007`：circuit open（熔断打开）。
8. `MGW_008`：internal adapter error（适配器内部错误）。

## 10. 日志与最小监控（v0.1）

尽管 `observability` 模块延期，`model-gateway` 仍需输出最小可排障数据：
1. 结构化日志字段：`trace_id`、`request_id`、`scene`、`task`、`provider`、`model`、`attempt`、`latency_ms`、`error_code`。
2. 进程内计数器（可先打印周期汇总）：
   - `mgw_requests_total`
   - `mgw_success_total`
   - `mgw_fallback_total`
   - `mgw_timeout_total`
   - `mgw_circuit_open_total`

## 11. 与其他模块交互约定

1. `orchestration` 只调用统一 Gateway 接口，不直接调用 Provider SDK。
2. `ingestion` 的 embedding 调用也必须通过 Gateway，保证配置与错误处理一致。
3. 上游不得依赖 Provider 原生错误码，只依赖 `MGW_*` 标准错误码。

## 12. 验收标准（DoD）

1. 至少接入 2 个 generation 模型配置，1 个 embedding 模型配置。
2. 主模型注入故障时，fallback 在 2 次尝试内可自动切换成功。
3. 单模型连续失败达到阈值后，熔断状态生效并阻断后续调用。
4. 所有失败响应均返回标准 `MGW_*` 错误码。
5. 成功响应包含 usage（token + estimated_cost）。
6. 压测下（并发 50）RAG 场景 model-gateway 额外开销 P95 <= 150ms。

## 13. 研发任务拆分（建议）

1. Adapter 层：封装 Provider SDK，统一输入输出结构。
2. Route Engine：实现场景路由、能力过滤、候选选择。
3. Fallback Engine：实现失败分类、重试、候选切换。
4. Circuit Breaker：实现滑窗统计与状态迁移。
5. Config Loader：加载并校验模型路由配置。
6. Contract Tests：对每个 Adapter 编写契约测试。
7. Chaos Tests：注入超时/429/5xx 验证 fallback 与熔断。

## 14. 后续演进（v0.2+）

1. 引入动态路由（按实时延迟、成本、成功率加权）。
2. 熔断状态共享化（Redis）并支持多实例一致决策。
3. 增加租户级配额与预算保护。
4. 与 `eval` 联动，按离线评分自动调优路由优先级。
5. 拆分为独立 `model-gateway` 微服务。
