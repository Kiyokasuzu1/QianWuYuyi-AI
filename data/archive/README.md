# data/archive/ 归档说明

> P2.2-A 数据治理实施（2026-08-19）产物。
> 本目录内所有内容均为**只读归档**：任何代码路径、测试、运行时都不应读写本目录。
> 归档原则：不删除历史数据，仅移出活跃数据面。

## 归档清单

### 1. memory_backup/
- **来源**：原 `data/memory_backup/`（4 个 20260802 时间戳快照）
- **时间**：2026-08-02
- **为什么归档**：旧桶时代（default 桶 / persist_test / test_user 混存）的 memory.json 历史快照，与现行扁平 list + user_id 结构不一致
- **当前状态**：不可作为 memory 恢复源直接覆盖活跃 memory.json；如需取证可只读查阅

### 2. memory_archive/
- **来源**：原 `data/memory_archive/`（3 个 system_ai_internal 归档）
- **时间**：2026-08-02
- **为什么归档**：system_pollution / ai_internal_pollution / invalid 污染桶的清理归档
- **当前状态**：只读保留

### 3. audit_snapshots/
- **来源**：原 `data/audit_4_4a/`（Phase 4.4a 生命周期测试快照，含 users/yuyi/relationship 快照）
- **时间**：Phase 4.4a 测试期
- **为什么归档**：测试快照混入生产数据目录
- **当前状态**：只读保留

### 4. proposals.jsonl / proposals.jsonl.fixed / proposals.jsonl.json_format_backup
- **来源**：原 `data/proposals/`（append-only 历史提案流，4,389 条，6.7 MB）
- **时间**：截至 2026-08-18
- **为什么归档**：
  1. 全部记录无 user_id 溯源，无法区分真实与测试来源
  2. 存在重复 id（如 `prop_97a2d5b13b04` ×28）与格式修复痕迹（.fixed / .json_format_backup）
  3. 活跃提案已切换至 `data/growth/proposals/proposals.json`（当前 0 提案）
- **当前不可作为 Growth 输入**：禁止任何 Growth/Runtime 回读本文件。新一轮提案流由 `src/growth/proposal_store.py` 在 `data/proposals/proposals.jsonl` 重新创建

### 5. cleanup_removed_20260819/
- **来源**：P2.2-A Phase 3 活跃文件清理的**逐行原始备份**
- **时间**：2026-08-19
- **内容**：
  - conversation_history.json（清理前全量）
  - experience_journal.jsonl（清理前全量）
  - audit_audit_logs.jsonl（清理前全量）
  - response_styles.jsonl（清理前全量）
  - cleanup_removed_*.jsonl（被移除的测试身份记录行）
- **为什么归档**：保留清理前原貌，可审计、可回滚
- **当前状态**：只读。若活跃文件验证无误，可长期保留

## 使用规则

1. 本目录**禁止**被 src/ 代码引用为读写路径。
2. 归档文件 hash 已在移动时逐文件校验一致（sha256）。
3. 需要恢复时：手工拷贝回原位置，并重跑对应测试套件。
