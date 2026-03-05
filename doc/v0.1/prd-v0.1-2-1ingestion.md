# Ingestion PRD（v0.1）

- 文档版本：v0.1
- 文档状态：可执行草案
- 日期：2026-03-05
- 所属项目：AI 平台（单服务形态）
- 模块路径：`apps/platform-service/modules/ingestion`
- 参考文档：
  - `doc/v0.1/prd-v0.1.md`
  - `doc/v0.1/prd-v0.1-model-gateway.md`
  - `doc/ai-platform-prd.md`

## 1. 背景与目标
`ingestion` 是 `platform-service` 内的数据摄取模块，负责把外部数据源内容转换为可检索索引，并保障增量更新、失败恢复与权限一致性。

v0.1 目标：
1. 打通 `parse -> chunk -> embed -> index` 的异步任务链路。
2. 建立统一 Job 状态机（含重试与 DLQ），保证失败可恢复。
3. 通过幂等键与版本哈希保证重复任务不会产生脏写。
4. embedding 必须统一走 `model-gateway`，避免模型调用策略分叉。
5. 为检索侧提供可追踪元数据（`doc_id/source_id/version_hash/acl_version`）。
6. 明确定义数据隔离边界（`tenant_id + app_id + source_id`）。

## 2. 范围定义

### 2.1 In Scope（v0.1 必做）
1. ingestion Job API：创建任务、查询状态、手动重试。
2. Queue + Worker 执行链路：`ingest.parse`、`ingest.chunk`、`ingest.embed`、`ingest.index`。
3. Job State Store（建议 PostgreSQL）与阶段状态持久化。
4. 指数退避重试与 DLQ（最大重试后进入死信）。
5. 幂等键去重与增量更新（基于 `version_hash`）。
6. 与 `model-gateway` 的 embedding 契约对接（scene=`ingestion`）。
7. 结构化日志与最小监控计数器。

### 2.2 Out of Scope（v0.1 不做）
1. 实时流式 ingestion（秒级 CDC 全量支持）。
2. 自动冲突合并（多连接器同文档冲突写入的智能裁决）。
3. 复杂数据清洗规则引擎（v0.1 仅做基础规范化）。
4. OCR/多模态解析（图片、音视频）增强能力。
5. 独立微服务拆分部署（v0.1 仅模块化实现）。

## 3. 模块职责与边界

上游调用方：
1. `api` 层：调用 `/api/v0.1/ingestion/jobs*` 接口发起任务。
2. 调度器（后续）：按计划任务触发周期同步。

下游依赖：
1. `libs/connectors`：拉取外部数据源变更。
2. `libs/parsers`：把原始文件解析为结构化文本。
3. `libs/chunking`：将文本切块并生成 chunk 元数据。
4. `model-gateway`：向量化（embedding）统一入口。
5. 向量库（Qdrant：`collection/point/vector/payload`）与元数据存储。

模块职责：
1. 任务接收与参数校验。
2. Job 编排、状态推进与错误归类。
3. 任务幂等控制与增量决策。
4. 阶段执行结果持久化与回放能力。
5. 输出可检索索引与可审计元数据。

## 4. 逻辑架构

```text
Ingestion API
   |
   v
Job Orchestrator
  ├─ Job State Store
  ├─ Queue Producer
  └─ Retry/DLQ Manager

Workers
  ├─ Parse Worker   (ingest.parse)
  ├─ Chunk Worker   (ingest.chunk)
  ├─ Embed Worker   (ingest.embed -> model-gateway/embed)
  └─ Index Worker   (ingest.index -> retrieval index stores)
```

说明：v0.1 与 `platform-service` 同进程代码库，运行时通过队列与 worker 池进行资源隔离。

## 5. 接口契约（模块对外）

### 5.1 创建任务：`POST /api/v0.1/ingestion/jobs`

请求示例：

```json
{
  "trace_id": "string",
  "request_id": "string",
  "tenant_id": "string",
  "app_id": "string",
  "source": {
    "source_id": "confluence-space-001",
    "source_type": "confluence|notion|drive|jira|git",
    "connector_config_ref": "cfg-001"
  },
  "sync_mode": "incremental|full",
  "trigger": "manual|schedule|event",
  "options": {
    "force_reindex": false,
    "chunk_policy": "default",
    "preferred_embedding_model": "optional-string"
  }
}
```

响应示例：

```json
{
  "ok": true,
  "job_id": "ing_20260305_0001",
  "status": "PENDING",
  "created_at": "2026-03-05T10:00:00Z"
}
```

### 5.2 查询任务：`GET /api/v0.1/ingestion/jobs/{job_id}`

响应示例：

```json
{
  "ok": true,
  "job_id": "ing_20260305_0001",
  "status": "RUNNING",
  "current_stage": "embed",
  "attempt": 2,
  "stages": {
    "parse": "SUCCEEDED",
    "chunk": "SUCCEEDED",
    "embed": "RUNNING",
    "index": "PENDING"
  },
  "stats": {
    "docs_total": 120,
    "docs_succeeded": 90,
    "docs_failed": 0,
    "chunks_total": 5200,
    "chunks_embedded": 3900
  }
}
```

### 5.3 手动重试：`POST /api/v0.1/ingestion/jobs/{job_id}/retry`

请求示例：

```json
{
  "reason": "manual retry after credential fix",
  "from_stage": "parse|chunk|embed|index|auto"
}
```

约束：
1. 仅 `FAILED` 或 `DEAD_LETTER` 任务可重试。
2. `from_stage=auto` 时从首个失败阶段继续执行。
3. 重试任务保留原 `job_id`，`attempt` 自增。

## 6. 数据模型与状态机

### 6.1 Job 关键字段
1. `job_id`、`tenant_id`、`app_id`、`source_id`、`source_type`
2. `status`：`PENDING|RUNNING|SUCCEEDED|FAILED|DEAD_LETTER`
3. `current_stage`：`parse|chunk|embed|index`
4. `attempt`、`max_attempts`
5. `idempotency_key`
6. `version_hash`、`acl_version`
7. `error_code`、`error_message`
8. `created_at`、`updated_at`、`finished_at`

### 6.2 状态迁移

```text
PENDING
  -> RUNNING
     -> SUCCEEDED
     -> FAILED -> RUNNING (retry)
     -> DEAD_LETTER
```

阶段内状态：

```text
STAGE_PENDING -> STAGE_RUNNING -> STAGE_SUCCEEDED|STAGE_FAILED
```

规则：
1. 任一阶段失败会写入 `FAILED` 并触发重试判断。
2. 超过 `max_attempts` 后进入 `DEAD_LETTER`。
3. `DEAD_LETTER` 仅支持人工重试恢复。

## 7. 阶段执行设计

### 7.1 parse 阶段
1. 从连接器拉取变更文档（新增/更新/删除）。
2. 解析文档为标准结构：`doc_id/title/file_name/file_type/content/source_updated_at/acl`。
3. 生成 `version_hash`（内容与关键元数据哈希）。

### 7.2 chunk 阶段
1. 按策略切块（默认固定窗口 + 重叠）。
2. 生成 chunk 元数据：`chunk_id/doc_id/order/token_count/chunk_source_ref`。
3. 保留 chunk 与文档映射及定位信息，供后续 `upsert points` 与删除回放。

`chunk_source_ref` 单字段规范（用于快速定位出处）：
1. 字段类型：`string`
2. 示例：
   - PDF：`pdf:p12`
   - Word：`word:para45`
   - Excel：`excel:Sheet1!R23`
   - Text/Code：`text:L120`

### 7.3 embed 阶段
1. 批量调用 `model-gateway` embedding 接口。
2. 请求必须带 `scene=ingestion` 与 `deadline_ms`。
3. 支持 `preferred_embedding_model` 覆盖默认路由。

### 7.4 index 阶段（Qdrant）
1. 根据 `tenant_id/app_id/embedding_model` 路由到目标 `collection`。
2. 每个 chunk 写入一条 `point`（`id + vector + payload`）。
3. 对过滤字段维护 `payload index`（至少 `source_id`、`source_type`、`doc_id`、`file_name`、`deleted_at`）。
4. 软删通过更新 `payload.deleted_at`；硬删通过 `delete points` 物理删除。

### 7.5 向量库结构定义（Qdrant）
对象模型（v0.1）：
1. `Collection`：应用级向量容器，命名建议 `col_{tenant_id}_{app_id}_{embedding_model}`（生产可使用 hash 缩短）。
2. `Point`：向量记录单元，对应一个 chunk。
3. `Vector`：embedding 数组，维度由模型决定。
4. `Payload`：过滤与追踪字段。
5. `Vector Index`：Collection 内 ANN 索引（HNSW）。
6. `Payload Index`：用于 `source_id/source_type/doc_id/file_name/deleted_at` 过滤加速。

Qdrant数据结构：
Qdrant
│
├── Collection: documents
│
│    ├── Segment A
│    │
│    │    ├── Vector Storage
│    │    │      ├── Point 1
│    │    │      ├── Point 2
│    │    │      └── Point 3
│    │    │
│    │    ├── Payload Storage
│    │    │
│    │    ├── Vector Index (HNSW)
│    │    │
│    │    └── Payload Index
│    │
│    ├── Segment B
│    │
│    └── Segment C
│
└── Collection: images


Point 结构示例：

```json
{
  "id": "chunk_001_vh_xxx",
  "vector": [0.01, 0.02, 0.03],
  "payload": {
    "ingest_job_id": "ing_20260305_0001",
    "tenant_id": "tenant_a",
    "app_id": "app_rag",
    "source_id": "confluence-space-001",
    "source_type": "confluence",
    "doc_id": "doc_001",
    "file_name": "employee-handbook.pdf",
    "file_type": "pdf",
    "version_hash": "vh_xxx",
    "acl_version": "acl_v1",
    "chunk_source_ref": "pdf:p12",
    "deleted_at": null,
    "chunk_order": 12
  }
}
```

写入规则：
1. 使用 `upsert points` 写入，`point.id` 冲突时覆盖。
2. `version_hash` 未变化时跳过 `upsert`。
3. 软删更新 `payload.deleted_at`；硬删按 `doc_id/version_hash` 过滤删除 points。

隔离规则：
1. `collection` 路由键是 `tenant_id + app_id + embedding_model`。
2. 查询先按 `tenant_id/app_id/embedding_model` 选中 collection，再执行向量检索。
3. collection 内通过 `payload filter` 限定 `source_id/source_type/file_name/deleted_at`。
4. 如需流程级隔离，可在 payload 增加 `workflow_id` 并建立 payload index。

### 7.6 关键字段与数据库结构关系（v0.1）

| 关键字段 | PostgreSQL（Job State Store） | Qdrant（Vector DB） | 用途 |
|---|---|---|---|
| `job_id` | `ingestion_jobs.job_id`（PK） | `point.payload.ingest_job_id` | 任务追踪、回放定位 |
| `tenant_id` | `ingestion_jobs.tenant_id` | `collection` 命名维度 + `point.payload.tenant_id` | 租户隔离与审计 |
| `app_id` | `ingestion_jobs.app_id` | `collection` 命名维度 + `point.payload.app_id` | 应用隔离与审计 |
| `source_id` | `ingestion_jobs.source_id` | `point.payload.source_id` + `payload index` | 来源过滤 |
| `source_type` | `ingestion_jobs.source_type` | `point.payload.source_type` + `payload index` | 来源类型过滤 |
| `file_name` | 文档解析结果字段（来自 parse 输出） | `point.payload.file_name` + `payload index` | 文件名过滤 |
| `chunk_source_ref` | chunk 阶段产出字段 | `point.payload.chunk_source_ref` | chunk 出处定位（单字段） |

约束：
1. `job_id` 是任务主键，不参与向量检索过滤。
2. `tenant_id/app_id/embedding_model` 决定写入和查询的 collection 路由。
3. `source_id/source_type/file_name` 仅在 collection 内作为 payload filter 生效。
4. `chunk_source_ref` 用于检索结果回溯定位，不作为主隔离键。

### 7.7 PostgreSQL 与 Qdrant 对应关系图（v0.1）

```text
PostgreSQL
  table: ingestion_jobs
    - job_id (PK)
    - tenant_id
    - app_id
    - source_id
    - source_type
            |
            | (parse/chunk/embed/index)
            v
Qdrant
  collection: col_{tenant_id}_{app_id}_{embedding_model}
    point:
      - id (chunk_id or chunk_id+version_hash)
      - vector
      - payload.ingest_job_id  <- job_id
      - payload.tenant_id      <- tenant_id
      - payload.app_id         <- app_id
      - payload.source_id      <- source_id
      - payload.source_type    <- source_type
      - payload.file_name      <- parse.file_name
      - payload.chunk_source_ref <- chunk.chunk_source_ref
```

## 8. 与 model-gateway 的交互约定

`ingestion` 只通过统一接口做向量化，不直接调用 Provider SDK。

请求示例（模块内）：

```json
{
  "trace_id": "string",
  "request_id": "string",
  "app_id": "string",
  "scene": "ingestion",
  "texts": ["chunk text 1", "chunk text 2"],
  "routing": {
    "preferred_model": "optional-string",
    "allow_fallback": true,
    "max_fallback_hops": 1
  },
  "deadline_ms": 15000
}
```

失败语义映射（建议）：
1. `MGW_003|MGW_005|MGW_007`：可重试（retryable）。
2. `MGW_004`：可重试但优先退避（模型整体不稳定）。
3. `MGW_006|MGW_002`：不可重试，直接失败并待人工修复。

## 9. 幂等与增量更新

幂等键：
`tenant_id + app_id + source_id + document_id + version_hash + stage`

规则：
1. 同幂等键重复执行时返回已存在结果，不重复执行 `upsert points`。
2. `version_hash` 未变化时跳过 `chunk/embed/index`。
3. 文档删除事件触发软删；硬删由异步清理任务完成。
4. ACL 变更单独更新 `acl_version`，触发检索侧回放。

## 10. 重试、退避与 DLQ

默认策略：
1. 最大重试次数：5。
2. 退避策略：`1m -> 5m -> 15m -> 30m -> 60m`。
3. 超限进入 DLQ，记录失败阶段与最后错误。

可重试错误：
1. 网络异常、连接超时、下游 `429/5xx`。
2. 临时性 `collection/point` 写入失败（可恢复）。

不可重试错误：
1. 参数非法、schema 不兼容。
2. 凭证失效或权限错误（需人工修复）。
3. 数据不可解析且不满足降级策略。

## 11. 错误码规范（ingestion）

1. `IGT_001`：job not found
2. `IGT_002`：invalid request params
3. `IGT_003`：job state conflict（状态冲突）
4. `IGT_004`：connector unavailable
5. `IGT_005`：parse failed
6. `IGT_006`：chunk failed
7. `IGT_007`：embedding failed
8. `IGT_008`：point upsert failed
9. `IGT_009`：retry exhausted（进入 DLQ）
10. `IGT_010`：idempotency conflict

## 12. 日志与最小监控（v0.1）

结构化日志字段：
1. `trace_id`、`request_id`、`job_id`、`source_id`
2. `stage`、`attempt`、`status`、`latency_ms`
3. `error_code`、`retryable`、`worker_id`

核心计数器：
1. `ingestion_jobs_total`
2. `ingestion_jobs_success_total`
3. `ingestion_jobs_failed_total`
4. `ingestion_jobs_dlq_total`
5. `ingestion_stage_retry_total`
6. `ingestion_embed_calls_total`
7. `ingestion_embed_fail_total`

## 13. 验收标准（DoD）

1. `POST/GET/retry` 三个 ingestion Job API 在 staging 可用。
2. `parse -> chunk -> embed -> index` 四阶段可完整跑通。
3. 故障注入下（超时、429、collection 暂时不可用）可通过重试恢复。
4. 超过重试上限任务进入 DLQ，且可人工重试恢复。
5. embedding 全量调用通过 `model-gateway`，不存在直连 Provider 的代码路径。
6. 幂等测试通过：同一 `idempotency_key` 不产生重复 `point upsert`。
7. 任务最终成功率（含重试）在压测样本下 >= 99.5%。
8. 向量检索请求可按 `tenant_id + app_id + source_id` 正确过滤数据范围。
9. 向量检索请求可按 `tenant_id + app_id + source_type` 正确隔离不同来源类型数据。
10. 向量检索请求可按 `file_name` 过滤指定文件的 chunk 数据。
11. 向量检索结果必须返回 `chunk_source_ref`，可直接定位 PDF 页码、Word 段落或 Excel 行号。

## 14. 研发任务拆分（建议）

1. API 层：实现任务创建、状态查询、手动重试接口。
2. State Store：定义 Job/Stage 表结构与状态迁移原子更新。
3. Queue 层：生产与消费 `parse/chunk/embed/index` 四类任务。
4. Worker 层：实现四阶段执行器与失败分类。
5. Gateway Client：封装 embedding 调用与错误码映射。
6. Idempotency：实现幂等键检查与结果复用。
7. Tests：补齐单测（状态机、错误映射）与集成测试（故障注入、DLQ）。

## 15. 后续演进（v0.2+）

1. 引入 OCR/多模态解析，支持图片与扫描件文档。
2. 增加策略化 chunking（语义块、代码块、表格块）。
3. 接入流式变更（CDC）与更细粒度增量索引。
4. 提供租户级并发配额与成本预算保护。
5. 拆分独立 `ingestion-service` 并支持独立扩缩容。
