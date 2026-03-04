# AI 平台基建 PRD（LangChain + LangGraph）

- 文档版本：v1.2
- 状态：草案
- 日期：2026-03-04
- 面向对象：平台研发、应用研发、数据治理、安全与运维团队

## 0. v1.2 变更摘要
1. 当前部署形态调整为单服务：`apps/platform-service`。
2. 微服务拆分改为后续演进策略，不作为一期交付要求。
3. 保留 v1.1 的三项关键增强：
   - ingestion 异步任务基础设施（Queue + Job State + DLQ + Retry）
   - 服务粒度分阶段拆分策略
   - 检索索引与元数据生命周期（版本、软删/硬删、ACL 回放）

## 1. 项目背景
公司内多个业务线正在尝试接入大模型，但当前存在以下问题：

1. 各团队重复建设 RAG、Agent、模型接入与安全治理能力，研发成本高。
2. 数据接入、索引更新、权限控制缺少统一规范，导致回答质量与合规风险不稳定。
3. 模型成本、延迟、稳定性缺少统一路由与观测手段，难以规模化运营。

因此需要建设统一 AI 平台作为公司级基建，沉淀可复用能力，并以标准化服务方式支撑业务快速落地。

## 2. 产品目标与成功指标

### 2.1 产品目标
1. 提供统一的 RAG 与 Agent 编排能力，支持业务快速上线智能应用。
2. 提供可治理的数据接入、检索、评测、观测、安全体系。
3. 通过模型网关与策略路由实现成本、质量与可用性的平衡。

### 2.2 成功指标（首期）
1. 新业务接入平台时间：从 4-8 周缩短到 1-2 周。
2. RAG 问答可引用率（带 citation）：>= 95%。
3. 检索结果 ACL 误放行率：0（高优先级合规指标）。
4. 线上请求可观测覆盖率（trace + metrics）：>= 99%。
5. 模型单位请求成本：较业务自建平均降低 >= 20%。

## 3. 范围定义

### 3.1 In Scope（本期建设）
1. 单服务 `platform-service`，统一承载 API 网关、编排、检索、ingestion、模型路由。
2. 数据 ingestion 全链路（连接器、解析、切块、向量化、增量更新）。
3. ingestion 异步任务基础设施（Queue、Job State、DLQ、Retry、幂等）。
4. 检索索引生命周期治理（版本、软删/硬删、ACL 回放）。
5. 基础安全能力（PII、Prompt Injection、防越权、审计）与可观测能力。

### 3.2 Out of Scope（本期不做）
1. 面向终端用户的完整业务前端产品。
2. 复杂多租户计费系统（仅保留成本归集与报表能力）。
3. 全自动 Agent 自主执行高风险动作（默认人工审批）。
4. 一期内拆分独立微服务集群（留到后续阶段）。

## 4. 用户角色与核心场景
1. 应用研发团队：调用平台 API 构建 Copilot、问答助手、流程 Agent。
2. 数据管理员：配置数据源接入、同步周期、权限映射。
3. 平台运维/SRE：监控服务健康、容量与告警。
4. 安全与合规团队：审计模型输入输出、策略命中、权限合规。

核心场景：
1. 企业知识问答（RAG）：知识库问答、可追溯引用。
2. 任务型 Agent：多步工具调用、状态机编排、可回放。
3. 研发知识助手：代码仓库 + 文档检索 + 工单系统联动。

## 5. 总体架构（单服务先行）

当前采用单服务部署：`apps/platform-service`。

- 在线模块：`api_gateway`、`orchestration`、`retrieval`、`model_gateway`
- 离线模块：`ingestion`（异步任务 worker）
- 治理模块：`security`、`prompt_hub`、`eval`、`observability`
- 复用层：`libs/*`（schema、连接器、解析器、向量封装、工具库）

请求主链路：
1. 请求进入 `platform-service` 的网关模块（鉴权、限流、审计）。
2. 编排模块（LangGraph）选择 RAG、Agent 或混合流程。
3. 检索模块执行 hybrid retrieve + ACL filter + rerank + citation。
4. 模型网关模块执行模型路由、fallback、成本统计。
5. 安全模块输出过滤；观测模块记录 trace/metrics/logs。

说明：模块边界按微服务接口设计，部署边界暂不拆分。

## 6. 模块设计与论证

### 6.1 单服务内模块划分

| 模块 | 主要职责 | 为什么在一期放入单服务 |
|---|---|---|
| `api_gateway` | 鉴权、限流、租户识别、审计、统一入口 | 统一入口能力必须优先，放在同服务可减少调用链复杂度 |
| `orchestration` | LangGraph 流程编排、状态管理、工具调用调度 | 需求变化快，同服务迭代效率更高 |
| `retrieval` | 混合检索、ACL、重排、citation、索引生命周期 | RAG 质量核心模块，与编排共进程可降低首期延迟 |
| `ingestion` | ETL、解析、切块、embedding、增量索引、任务编排 | 长任务在同代码库内开发更快，但通过异步队列隔离资源 |
| `model_gateway` | 多模型接入、路由策略、熔断降级、成本统计 | 统一模型策略控制点，先模块化避免跨服务改造成本 |
| `security` | 注入攻击检测、PII 过滤、策略拦截 | 安全策略先中心化实现，避免各模块重复规则 |
| `prompt_hub` | Prompt 模板、版本、灰度发布 | Prompt 作为生产资产先统一管理 |
| `eval` | 离线评测、golden 回归、发布门禁 | 先做模块化门禁，后续独立服务化 |
| `observability` | Trace、Metrics、Logs 采集与告警 | 一期保证可观测闭环，降低故障定位成本 |

### 6.2 libs 模块

| 模块 | 主要职责 | 设计论证 |
|---|---|---|
| `core-types` | 统一 State/Chunk/Citation/Request schema | 防止协议漂移，支持后续服务拆分时保持契约稳定 |
| `connectors` | Confluence/Notion/Drive/Jira/Git 接入插件 | 高复用、高变更，库化后便于扩展 |
| `parsers` | PDF/HTML/DOCX/Code 解析 | 文档格式差异大，统一解析提高稳定性 |
| `chunking` | 切块策略库（固定窗、语义块、代码块） | 便于 A/B 与场景化优化 |
| `embeddings` | embedding provider 封装 | 降低模型供应商替换成本 |
| `security` | 安全检测与策略函数库 | 供在线链路与离线链路复用 |
| `prompt-hub` | Prompt 管理能力库 | 供编排/评测共享同一模板版本 |
| `utils` | 重试、缓存、序列化、ID 生成 | 降低重复代码 |

### 6.3 infra/configs/tests 模块
1. `infra/helm`：标准化 K8s 发布，支持多环境一致部署。
2. `infra/terraform`：云资源声明式管理，确保可审计与可回滚。
3. `configs/env`：环境参数分层（dev/staging/prod），避免硬编码。
4. `configs/prompts`：Prompt 配置与灰度策略集中管理。
5. `tests/e2e`：验证完整链路（网关-编排-检索-模型）。
6. `tests/golden`：固定样本回归，保障质量稳定。

结论：一期以“逻辑模块化 + 单服务部署”降低复杂度，后续以“契约稳定 + 按阈值拆分”控制演进风险。

### 6.4 v1.1 核心增强（v1.2 继承）

#### 6.4.1 Ingestion 异步任务基础设施
1. 引入 Queue（Kafka/RabbitMQ/SQS 任一）承载 `ingest.parse`、`ingest.chunk`、`ingest.embed`、`ingest.index` 任务主题。
2. 建立 Job State Store（建议 PostgreSQL）维护状态机：`PENDING -> RUNNING -> SUCCEEDED/FAILED -> DEAD_LETTER`。
3. 重试策略：指数退避（1m/5m/15m）+ 最大重试次数（默认 5 次）+ 超限入 DLQ。
4. 幂等键：`tenant_id + source_id + document_id + version_hash + stage`。
5. 任务可观测：记录 `trace_id`、`attempt`、`error_code`、`worker_id`。

#### 6.4.2 后续微服务拆分策略
1. 阶段 A（当前）：单服务 `platform-service`。
2. 阶段 B：先拆高耦合低风险边界（通常先拆 `ingestion`，再拆 `retrieval` / `model_gateway`）。
3. 阶段 C：拆治理能力（`eval`、`prompt_hub`、`security`）并独立发布。
4. 拆分阈值建议：
   - 单模块日调用 > 100 万；
   - 与主链路发布节奏显著不同；
   - 需要独立扩缩容或独立合规边界。

#### 6.4.3 检索索引与元数据生命周期
1. 元数据强制字段：`doc_id`、`source_id`、`version_hash`、`source_updated_at`、`acl_version`、`deleted_at`。
2. 文档状态机：`ACTIVE -> SOFT_DELETED -> HARD_DELETED`。
3. 删除传播：软删实时过滤，硬删异步清理（向量库 + 倒排索引双删）。
4. ACL 变更回放：ACL 事件入队，检索侧增量回放并记录延迟。
5. 一致性目标：索引与元数据最终一致，可观测 `acl_replay_lag_seconds`。

## 7. 关键流程设计

### 7.1 RAG 在线问答流程
1. 请求进入 `platform-service/api_gateway`。
2. `orchestration` 选择 RAG graph（query rewrite -> retrieve -> rerank -> answer）。
3. `retrieval` 执行 hybrid retrieve + ACL + rerank。
4. `model_gateway` 选择最优模型生成答案。
5. 返回答案与 citation，同时上报可观测数据。

### 7.2 Agent 执行流程
1. LangGraph 定义状态节点（计划、工具调用、观察、反思、结束）。
2. 工具调用通过统一 Tool Registry。
3. 高风险动作（写操作）默认进入审批节点。
4. 全过程可回放（state snapshot + tool logs）。

### 7.3 数据摄取与增量更新流程（异步化）
1. `ingestion` 读取 `connectors` 变更并投递任务到 Queue。
2. Worker 消费任务执行 `parse -> chunk -> embed -> index`。
3. 每阶段写回 Job State Store，记录 `attempt` 与失败原因。
4. 失败按 Retry Policy 重试，超限进入 DLQ。
5. 依据版本戳/哈希做增量更新，幂等键去重。

### 7.4 检索索引生命周期流程
1. 首次入库：写入 `ACTIVE + version_hash`。
2. 更新：生成新版本，旧版本按策略软删或保留回滚窗口。
3. 删除：先 `SOFT_DELETED`，再后台 `HARD_DELETED`。
4. ACL 变更：写入事件流并回放到检索元数据。

## 8. 功能需求（FR）

1. FR-01：统一身份认证（OAuth2/JWT）与租户隔离。
2. FR-02：支持至少 5 类企业数据源接入（Confluence/Notion/Drive/Jira/Git）。
3. FR-03：检索支持关键词+向量混合召回与 rerank。
4. FR-04：检索结果必须执行 ACL 过滤并返回 citation。
5. FR-05：编排支持 LangGraph 多节点流程、重试、超时、中断恢复。
6. FR-06：模型网关支持多模型路由、fallback、熔断、成本统计。
7. FR-07：Prompt Hub 支持版本化、灰度发布、回滚。
8. FR-08：评测支持离线批评测、golden 回归与报告。
9. FR-09：可观测支持 trace/metrics/logs 关联查询。
10. FR-10：安全支持 prompt injection 检测与 PII 脱敏。
11. FR-11：ingestion 必须采用 Queue + Job State + DLQ + Retry。
12. FR-12：检索必须支持版本、软删/硬删、回滚窗口。
13. FR-13：ACL 变更必须事件回放且可观测延迟。
14. FR-14：一期单服务部署，预留后续微服务拆分接口契约。

## 9. 非功能需求（NFR）

1. 可用性：核心在线链路月度可用性 >= 99.9%。
2. 性能：P95（RAG）<= 5s，P99 <= 10s。
3. 扩展性：当前服务可水平扩展；后续支持模块独立拆分。
4. 安全性：全链路 TLS；敏感数据传输与存储加密；审计可追踪。
5. 可维护性：统一 schema、标准日志字段、标准错误码。
6. 成本控制：模型调用按租户/应用/模型维度出账与告警。
7. 数据一致性：文档更新到可检索结果 P95 <= 10 分钟。
8. ACL 一致性：ACL 变更到检索生效 P95 <= 5 分钟。
9. 任务可靠性：ingestion 异步任务最终成功率 >= 99.9%（含重试）。

## 10. 技术选型原则

1. 编排框架：LangGraph（复杂 Agent 状态机）、LangChain（通用链路组件）。
2. API 层：FastAPI 或 Go（按团队主力语言与性能要求决策）。
3. 检索层：向量库 + 关键词检索引擎（如 OpenSearch/Elastic + 向量引擎）。
4. 可观测：OpenTelemetry + Prometheus + 日志系统。
5. 部署：Kubernetes + Helm；云资源由 Terraform 管理。
6. 异步任务：Kafka/RabbitMQ/SQS（三选一）。
7. 任务状态存储：PostgreSQL（主）+ Redis（可选）。

## 11. 里程碑规划（建议）

1. M1（第 1-4 周）：完成 `platform-service` 单服务骨架（网关、编排、检索、ingestion、模型网关）+ 基础观测。
2. M2（第 5-8 周）：完成 ingestion 异步基础设施（Queue/Job State/DLQ/Retry）与幂等机制。
3. M3（第 9-12 周）：完成检索生命周期（版本、软删/硬删、ACL 回放）+ golden 回归门禁。
4. M4（第 13-16 周）：按阈值开始拆分首个微服务（建议 ingestion 或 retrieval）。

## 12. 风险与应对

1. 单服务复杂度上升：通过模块边界、接口契约和代码分层控制。
2. 检索质量不足导致幻觉：通过 rerank、query rewrite、golden 回归持续优化。
3. 权限映射复杂导致越权：ACL 强制前置 + ACL 回放监控 + 审计抽检。
4. 索引生命周期失控导致脏数据：版本戳、软删/硬删、回滚窗口。
5. ingestion 长任务失败堆积：Queue + Job State + Retry + DLQ + 值班流程。
6. 模型供应不稳定：模型网关内置 fallback、熔断、超时降级。
7. 成本失控：按应用/租户设置预算阈值与限额策略。

## 13. 验收标准（DoD）

1. 完成 `apps/platform-service` 单服务结构与统一 schema。
2. 打通至少 2 个真实业务场景（RAG 问答 + Agent 流程）。
3. `tests/e2e` 与 `tests/golden` 在 staging 稳定通过。
4. 可观测面板覆盖核心 SLA/SLO 指标并配置告警。
5. 安全与合规评审通过（ACL、PII、审计、日志留存策略）。
6. ingestion 任务状态机与 DLQ 在 staging 经故障注入验证通过。
7. 检索层软删/硬删与 ACL 回放端到端一致性验证通过。

## 14. 附录：目录映射（当前与目标态）

### 14.1 当前目录（一期）

```text
ai-platform/
  apps/
    platform-service/
  libs/
    core-types/
    connectors/
    parsers/
    chunking/
    embeddings/
    security/
    prompt-hub/
    utils/
  infra/
    helm/
    terraform/
    docker/
  configs/
    env/
    prompts/
  tests/
    e2e/
    golden/
  doc/
```

### 14.2 目标态目录（后续微服务拆分）

```text
ai-platform/
  apps/
    api-gateway/
    orchestration-service/
    retrieval-service/
    ingestion-service/
    model-gateway/
    eval-service/
    observability/
```

该演进方式确保：
1. 一期交付速度与运维复杂度可控。
2. 二期在契约稳定前提下平滑拆分微服务。
3. 治理能力（安全、评测、观测）全程可持续演进。
