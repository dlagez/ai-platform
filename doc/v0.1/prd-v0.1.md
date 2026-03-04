# AI 平台 PRD（v0.1）

- 文档版本：v0.1
- 文档状态：可执行草案
- 日期：2026-03-04
- 部署形态：单服务（`apps/platform-service`）
- 技术基座：LangChain + LangGraph

## 1. 版本目标
v0.1 目标是交付一个可运行的最小 AI 平台版本，优先打通 RAG 与基础 Agent 主链路，支持首个业务接入。

成功标准：
1. 支持至少 1 个业务应用接入并稳定运行。
2. 打通“数据接入 -> 索引更新 -> 在线问答 -> citation 返回”的闭环。
3. 异步任务失败可重试、可恢复。

## 2. 用户与场景
用户角色：
1. 应用研发：调用统一 API 快速接入问答与 Agent。
2. 数据管理员：管理数据源同步任务与权限映射。

核心场景（v0.1）：
1. 企业知识问答（RAG）：返回答案 + citation。
2. 受限 Agent 执行：允许只读工具调用，不允许高风险写操作自动执行。

## 3. 范围定义

### 3.1 In Scope（v0.1 必做）
1. 单服务架构：`platform-service` 内实现编排、检索、ingestion、模型路由四个核心模块。
2. RAG 在线链路：query rewrite（可选）+ hybrid retrieve + rerank + answer + citation。
3. ingestion 异步任务：Queue + Job State + Retry + DLQ + 幂等键。
4. 索引生命周期：版本戳、软删/硬删、ACL 事件回放。
5. 基础接口能力：`/ask`、`/chat`、`/ingestion/jobs`、`/healthz`。

### 3.2 Out of Scope（v0.1 不做）
1. `api-gateway`：鉴权、限流、审计、错误码标准化能力延后。
2. `security`：注入检测、PII 检测/脱敏、策略拦截延后。
3. `eval`：golden set 执行、回归评分、报告导出延后。
4. `observability`：trace/metrics/logs 采集与告警延后。
5. 多服务部署与服务网格治理。
6. 自动执行高风险工具（写库、发单、审批）。
7. 面向终端用户的完整前端产品。

## 4. 功能需求（按优先级）

### 4.1 P0（必须）
1. `/ask` 与 `/chat`：支持上下文问答，输出 citation。
2. 数据接入任务 API：创建同步任务、查询任务状态、失败重试。
3. 检索能力：关键词 + 向量混合召回，ACL 过滤后返回候选片段。
4. 模型路由：至少支持 2 个模型提供方或 2 个模型配置，支持 fallback。
5. 任务状态机：`PENDING/RUNNING/SUCCEEDED/FAILED/DEAD_LETTER`。
6. 健康检查：`/healthz`、`/readyz`。

### 4.2 P1（建议）
1. Prompt 模板版本管理与灰度开关。
2. rerank 策略可配置（按租户/应用）。
3. 任务并发控制与背压策略（防止队列堆积击穿）。

### 4.3 P2（可后置）
1. 统一网关能力（鉴权、限流、审计）。
2. 安全策略中心（注入检测、PII）。
3. 离线评测与自动回归门禁。
4. 全链路可观测（trace/metrics/logs + 告警）。

## 5. 单服务模块划分（实现边界）
目录建议：

```text
apps/platform-service/
  modules/
    orchestration/
    retrieval/
    ingestion/
    model-gateway/
```

模块职责：
1. `orchestration`：LangGraph 流程编排、Agent 状态管理。
2. `retrieval`：混合检索、ACL 过滤、rerank、citation 组装。
3. `ingestion`：异步 ETL、解析、切块、embedding、索引更新。
4. `model-gateway`：模型选择、fallback、超时与熔断、成本统计。

说明：`api-gateway/security/eval/observability` 目录可保留占位，但 v0.1 不实现功能。

## 6. 关键流程

### 6.1 在线问答流程（RAG）
1. 请求进入 `platform-service` API 层。
2. `orchestration` 选择 RAG 图执行。
3. `retrieval` 完成召回、ACL、重排并返回上下文片段。
4. `model-gateway` 选择模型生成答案并返回 citation。

### 6.2 ingestion 异步流程
1. 外部数据变更触发任务入队。
2. Worker 执行 `parse -> chunk -> embed -> index`。
3. 每阶段写入 Job State Store。
4. 失败按 Retry Policy 重试，超过阈值进入 DLQ。
5. 文档按 `version_hash` 增量更新并幂等去重。

### 6.3 ACL 与索引生命周期
1. 文档状态：`ACTIVE -> SOFT_DELETED -> HARD_DELETED`。
2. 软删即时查询过滤，硬删异步清理向量与倒排索引。
3. ACL 变更事件回放更新索引元数据，统计回放延迟。

## 7. v0.1 接口清单（最小）
1. `POST /api/v0.1/ask`
2. `POST /api/v0.1/chat`
3. `POST /api/v0.1/ingestion/jobs`
4. `GET /api/v0.1/ingestion/jobs/{job_id}`
5. `POST /api/v0.1/ingestion/jobs/{job_id}/retry`
6. `GET /api/v0.1/healthz`
7. `GET /api/v0.1/readyz`

## 8. 非功能要求（NFR）
1. 可用性：月可用性 >= 99.5%（v0.1）。
2. 性能：RAG 请求 P95 <= 6s，P99 <= 12s。
3. 一致性：文档更新到可检索生效 P95 <= 10 分钟。
4. ACL 生效：ACL 变更传播 P95 <= 5 分钟。
5. 可靠性：ingestion 最终成功率 >= 99.5%（重试后）。

## 9. 验收标准（DoD）
1. `apps/platform-service` 下完成 4 个核心模块骨架：`orchestration/retrieval/ingestion/model-gateway`。
2. 至少接入 2 类数据源（例如 Confluence + Git）。
3. RAG 流程可返回 citation，且 ACL 误放行率为 0。
4. 故障注入测试下，任务可通过重试/DLQ 恢复。
5. 核心 API（`/ask`、`/chat`、`/ingestion/jobs`）在 staging 稳定可用。

## 10. 里程碑（建议 8-10 周）
1. W1-W2：单服务骨架 + 编排最小链路。
2. W3-W4：检索与模型路由打通，支持 `/ask`。
3. W5-W6：ingestion 异步任务体系与索引生命周期完成。
4. W7-W8：接口稳定性与性能调优，完成首业务接入。
5. W9-W10：补齐上线前压测与发布准备。

## 11. 后续演进（v0.2+）
1. 增加 `api-gateway`：鉴权、限流、审计、标准错误码。
2. 增加 `security`：注入检测、PII 检测/脱敏、策略拦截。
3. 增加 `eval`：golden set 执行、回归评分、报告导出。
4. 增加 `observability`：trace/metrics/logs 与告警。
5. 视流量与团队边界逐步拆分微服务。
