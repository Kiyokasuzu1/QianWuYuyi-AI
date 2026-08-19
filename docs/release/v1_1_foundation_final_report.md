# QianWuYuyi-AI V1.1 Foundation Hardening 最终报告

> 阶段：V1.1 Foundation Hardening（全流程自主执行）
> 日期：2026-08-16
> 前置文档：`data/validation/v1_1_foundation_audit.md`（Phase 0 只读审计，风险表 F1–F7）
> 结论先行：**达到长期运行标准（有条件通过）**——Authority 无分裂、关键持久化安全、测试默认隔离、冒烟测试通过、无新增 A 类失败。

---

## 1. 完成内容

按审计风险表 F1–F7 逐项处置（只自动处理「必须修复」；建议修复仅记录）：

| 编号 | 内容 | 处置 |
|------|------|------|
| F1 | ProposalStorage 相对路径单例随 cwd 漂移（P1） | ✅ 构造时 `resolve()` 绝对路径锚定 + `reset_for_testing()` + conftest 每测试重置 + 3 个专项测试 |
| F2 | 12 处生产路径裸写（P0/P1） | ✅ 全部接入 `atomic_write_json` + `get_path_lock`（读-改-写持锁）+ 损坏加载侧 `backup_corrupt_file` 备份 |
| F3 | conftest 正则漏掉 get_* 访问器（30 文件隔离漏洞，P2） | ✅ 正则扩展 8 个访问器名 + ProposalStorage 单例重置注册 + 会话级真实数据污染探针（非阻断 WARNING） |
| F4 | test_phase381_semantic_consistency 真实 LLM 未标记（P2） | ✅ 整模块 `pytestmark = pytest.mark.real_api`，默认跳过（4 skipped） |
| F5 | data/emotions + data/growth 5 个测试残留文件（P2） | ✅ 备份 + sha256 记录 + 删除，记录 `cleanup_test_junk_f5_record.json` |
| F6 | ~14 处建议修复（裸写/相对路径默认值/YuyiPersistence 写路径） | 📝 仅记录，未处理（休眠写路径、非生产主链路，避免扩大范围） |
| F7 | 366648462.json 19:17 写入来源不明 | 📝 不触碰，仅记录（真实用户数据，无法判定污染） |

关键工具扩展：`src/memory/atomic_write.py` 新增 `get_path_lock()`（进程级按绝对路径 RLock）与 `backup_corrupt_file()`（copy 不 move，损坏现场保留）——新增能力，非新系统、非新存储设计。

## 2. 修改文件列表

**生产代码（14 个文件）**：

| 文件 | 修改 |
|------|------|
| `src/memory/atomic_write.py` | +`get_path_lock()` +`backup_corrupt_file()`（原 68 行 → 新增 ~55 行） |
| `src/growth/proposal/storage.py` | `data_dir = Path(data_dir).resolve()`；+`ProposalStorage.reset_for_testing()` |
| `src/personality/personality_evolution_pipeline.py` | `_persist` 原子写+锁；`_load_history` 损坏备份 |
| `src/personality/self_model_snapshot.py` | `_save` 原子写+锁（保留 default=str）；`_load` 损坏备份 |
| `src/personality/self_narrative_history.py` | `save` 原子写+锁；`load` 补 try/except + 损坏备份（此前损坏直接抛异常） |
| `src/emotion/emotion_pattern_repository.py` | `save_all` 原子写+锁；`append` 读-改-写全程持锁；`load_all` 损坏备份 |
| `src/orchestrator.py` | per-user 情绪降级写 → `_atomic_write_emotion_fallback()`（锁+原子写，2 处） |
| `src/orchestrator_hooks.py` | 同款降级写 2 处 → 锁+原子写 |
| `src/runtime/personality_event_bus.py` | `_save_history` 锁+原子写；`_load_history` 损坏备份 |
| `src/runtime/reflection_growth_bridge.py` | `_persist_history` 锁+原子写；加载损坏备份 |
| `src/runtime/adapters/growth_proposal_mirror.py` | `_init_file`/`_save` 锁+原子写；`_load` 损坏备份 |
| `src/runtime/adapters/growth_adapter.py` | `_save` 锁+原子写；`_load` 损坏备份 |
| `src/runtime/lifecycle_manager.py` | `_save_history_to_file` 锁+原子写；加载损坏备份 |
| `src/runtime/pipeline/runtime_growth_pipeline.py` | `_save_runs` 锁+原子写（保留 default=str）；`load_runs` 损坏备份 |

**测试代码（5 个文件）**：

| 文件 | 说明 |
|------|------|
| `tests/growth/test_proposal_storage_path_stability.py` | 新增：F1 跨 cwd 锚定 / 重载一致 / 单例重置（3 测试） |
| `tests/test_atomic_write_utils.py` | 新增：原子性 / 失败保旧文件 / 锁归一化 / 备份语义（5 测试） |
| `tests/foundation/test_v1_1_persistence_hardening.py` | 新增：emotion pattern / evolution / narrative 损坏恢复（6 测试） |
| `tests/foundation/v1_1_foundation_smoke_test.py` | 新增：多轮运行+重启+恢复全链路（6 测试） |
| `tests/conftest.py` | 正则扩展 8 个访问器 + ProposalStorage 重置注册 + 会话级污染探针 |
| `tests/runtime/test_phase381_semantic_consistency.py` | +`import pytest` + 模块级 real_api 标记 |

**修改前备份**：全部 16 个源文件 sha256 + 备份副本存于 `data/validation/v1_1_opt_backup/`（`sha256_manifest.json`）。

**数据处置记录**：F5 五个测试残留文件备份于 `data/validation/v1_1_opt_backup/junk_cleanup_f5/`，记录 `cleanup_test_junk_f5_record.json`。

## 3. 架构影响

- **数据格式零变化**：所有原子写保持原 json.dump 参数（ensure_ascii=False、indent=2 或原 default=str），无字段增删，无 schema 变更。
- **接口兼容**：全部为函数内部实现替换，无公共接口签名变化；`ProposalStorage.__init__` 行为升级（相对→绝对）对调用方透明，唯一可观察差异是单例跨 cwd 不再漂移（即修复目标本身）。
- **无新模块**：锁/备份助手挂载于既有 `atomic_write.py` 叶子模块，符合「不创建孤立系统」规则。
- **Authority 边界**：未触碰 RuntimeCore 权威链（runtime_core.py:519-532 / 3382 / 3694 / 3702 / 3763）、GrowthState stale-guard（growth_state.py:89-149/308）、IDENTITY_CORE、人格成长原则、Memory/Growth 算法。零 Authority 修改。
- **测试隔离语义**：隔离池 236 → 237 文件（净增 1：test_memory_dashboard_provider.py，验证转绿）；real_api 默认跳过新增 4 例；污染探针为非阻断告警，不改变 pass/fail 判定。

## 4. 测试统计

| 批次 | 结果 | A 类新增 |
|------|------|----------|
| 新增测试（4 文件） | 20 passed | — |
| 受影响模块定向回归（evolution/snapshot/narrative/emotion pattern/mirror/lifecycle/集成） | 188 passed | 0 |
| runtime+growth 全目录回归 | 263 passed / 3 failed / 4 skipped | 3 failed 全部与历史基线逐条吻合（B 类） |
| V1.0 核心回归（48 文件定向批次，两轮） | 1485 passed / **14 failed** / 4 skipped（两轮一致） | **0**（14/14 与 `p5_0_e_ab_before2.log`+`after2.log` 双侧基线逐条一致） |
| V1.0 修复点复验（3 例） | 2 passed / 1 skipped | 0 |
| V1.1 冒烟测试 | 6 passed | — |

V1.0 修复点复验明细：`test_memory_store_can_locate_qingxialing`、`TestE4SelfModelSync::test_self_model_and_history_written` 转绿保持；`TestE8PromptCapture` 跳过（历史原因，与 V1.0 报告一致）。

## 5. 数据完整性验证

- Phase 2/3 全程 13 个真实数据文件哈希快照（`.v11_phase2_data_snapshot.json`）对比：**全部无变化**（含 366648462.json 用户情绪数据、data/memory.json、data/self_model.json、data/growth_state.json、data/runtime_state.json、proposals.json 等）。
- F5 五个测试残留文件：确认已删除，备份+sha256 可恢复。
- 冒烟测试内嵌红线断言：真实 `data/growth/proposals/proposals.json` 哈希前后一致。
- 会话级污染探针在全部批次中未触发。

## 6. 剩余风险

| 风险 | 等级 | 说明 |
|------|------|------|
| ~14 处建议修复裸写（F6） | Medium | permission/approval/limiter/agreement/retry_queue/audit(no-lock append)/emotion counter 等非主链路或低频路径；V1.0 报告 R7/R8 已列明，作为 V1.2 候选 |
| 历史 B 类失败 14+3 例 | Medium | pipeline_server/prod_smoke/runtime_path_audit 等，全部为优化前既有失败，禁止为消灭历史失败大规模修改（任务约束） |
| 366648462.json 写入来源（F7） | Low | 无法判定污染，保持不触碰，长期观察 |
| `get_path_lock` 进程内锁 | Low | 跨进程并发仍由 os.replace 原子性兜底；单进程多线程场景已覆盖 |
| 损坏备份文件无自动清理 | Low | `.corrupt.*` 文件需人工/后续任务清理，当前保证不丢数据优先 |

## 7. 是否达到长期运行标准

**达到（有条件通过）**。对照任务完成标准逐项：

- [x] **Authority 无分裂**：V1.0 权威闭包修复经审计确认完好；本次未引入任何新权威路径。
- [x] **关键持久化安全**：12 处生产裸写全部原子化+持锁，加载损坏侧统一备份不覆盖；冒烟/回归/损坏注入测试全绿。
- [x] **测试默认隔离**：get_* 访问器漏洞收口（隔离池 237/513），单例逐测试重置，真实 LLM 默认跳过，污染探针上线且全程未触发。
- [x] **smoke test 通过**：多轮运行（启动→交互→Memory→SelfModel→Proposal→保存→Restart→恢复，含 cwd 漂移）6/6 通过。
- [x] **无新增 A 类失败**：核心回归 1485P/14F，14 例全部为双侧基线 B 类，0 新增。
- [x] **最终报告生成**：本文档。

V1.2 建议候选（不纳入本阶段范围）：F6 建议修复清单、历史 B 类失败治理、`.corrupt.*` 备份轮转策略、YuyiPersistence 旧写路径下线评估。
