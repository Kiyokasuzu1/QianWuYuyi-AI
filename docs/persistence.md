## Persistence Layer（Phase 3.5.22）

`PersistenceManager` 是统一持久化底座，用于逐步替换各模块分散的 JSON 文件写入方式。

设计目标：

- `append-only`：日志只追加，不覆盖历史
- `snapshot`：提供可快速加载的状态快照
- `schema_version`：每条记录携带版本号
- `migration`：允许按版本迁移记录
- `corruption detection`：使用 hash chain 检测篡改/损坏
- `backup`：为关键实体提供离线备份
- `auto recovery`：snapshot 缺失或损坏可从 log 重建

### 存储结构

默认目录：`data/persistence/`

- `global/<entity>/`
  - `log.jsonl`：追加式记录（每行一条 JSON）
  - `snapshot.json`：重建后的快照
  - `backups/<timestamp>/`：备份目录
- `users/<platform>_<user_id>/<entity>/`：按用户隔离的同构目录

### 记录格式

每条 log 记录包含：

- `ts`：时间戳
- `entity`：实体名（memory / growth_proposal / self_model 等）
- `record_type`：默认 `upsert`
- `schema_version`：版本号
- `data`：实体数据
- `prev_hash`：上一条记录 hash
- `hash`：本条记录 hash（对去掉 hash 的 payload 做 sha256）

### 注意事项

- PersistenceManager 只提供底座能力，不会自动修改人格、不自动接受 proposal。
- 旧模块仍可按原路径工作；迁移会在后续阶段逐步完成（以兼容为优先）。

