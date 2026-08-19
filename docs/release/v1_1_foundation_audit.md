# QianWuYuyi-AI V1.1 Foundation Hardening — Phase 0 只读审计

> 生成时间：2026-08-17（本地）
> 审计方式：只读 grep/阅读扫描，全程未修改任何文件
> 范围：A. Authority / B. Persistence / C. Runtime / D. Test Engineering
> 分类标准：必须修复（P0 长期数据损坏 / P1 状态分裂 / P2 测试污染）> 建议修复 > 未来优化

---

## A. Authority

### A1. V1.0 三项修复现状（复核通过 ✅）

| Authority | 机制 | 现状 |
|---|---|---|
| MemoryAuthority | 启动时 store identity 分裂检测 | `runtime_core.py:519-532` 检测 + `:3694 memory_authority_split_detected()`，完好 |
| SelfModelAuthority | `get_self_model_store()` resolver store 优先 | `runtime_core.py:3382` 统一入口完好 |
| GrowthStateAuthority | stale-write guard（磁盘 mtime）+ `resolve_authority_growth_state` | `growth_state.py:89-149, 308` 完好 |

### A2. 新发现：ProposalStorage 路径不稳定（必须修复，P1）

`src/growth/proposal/storage.py`：`__init__` 中 `self.data_dir = Path(data_dir)`（相对路径）+ `self.json_file = self.data_dir / "proposals.json"`。进程内 cwd 变化后，同一单例的读写在**不同目录**发生（V1.0 阶段 2d 隔离已实测复现：跨测试 cwd 切换 → 保存静默失败/写错位置）。

- 生产现状：cwd 稳定，未实际触发，属潜伏缺陷。
- 测试现状：conftest per-test tmp cwd 切换直接踩中。
- 修复方向：构造时 `resolve()` 绝对化 + `_global_storage` 增加 `reset_for_testing()`（并入 conftest 单例清理）。

### A3. 新发现：conftest 隔离正则漏掉 `get_*` 单例访问器（必须修复，P2）

V1.0 conftest `_SINGLETON_PATTERN` 覆盖构造器（`SelfModelStore(` 等），但**不覆盖访问器函数**：`get_self_model_store` / `get_proposal_storage` / `get_growth_state` / `get_emotion_repository` 等。经这些函数间接触碰单例的测试文件不会被隔离 → 写真实 data/。

静态扫描：30 个测试文件「含写操作 + 无模式匹配 + 无 tmp_path/mkdtemp/keep_repo_cwd 自隔离」（见 D1），其中大部分经访问器/模块函数路径写数据。

### A4. 其余 fallback 复核（记录，不处理）

- `orchestrator.py:194` SelfModelStore 紧急 fallback：仅 bridge+resolver 均不可用时触发，可接受（V1.0 已记）。
- `resolve_authority_growth_state` fallback 自建：已由 stale-write guard 兜底。
- `YuyiPersistence`（`storage/yuyi_persistence.py`）：写路径在生产中已休眠（仅 `self_model_store.py:380` / `runtime_core.py:591` 作 legacy 信封**读取**兼容），无 Authority 冲突。

---

## B. Persistence

### B1. 裸写清单（2026-08-17 复核）

**必须修复（生产活跃路径 + 用户指定方向 emotion/snapshot/self narrative/runtime 状态）**

| 文件:行 | 数据 | 等级 | 处置 |
|---|---|---|---|
| `src/personality/personality_evolution_pipeline.py:257` | 人格演化历史（Growth 演化记录） | **P0** | 原子写 + 锁 + 损坏备份 |
| `src/personality/self_model_snapshot.py:188` | SelfModel 快照 | P1 | 同上 |
| `src/personality/self_narrative_history.py:121` | 自我叙事历史 | P1 | 同上（load 无 try/except，损坏直接抛错） |
| `src/emotion/emotion_pattern_repository.py:25` | 情绪模式仓库（append=load-modify-save 无锁） | P1 | 同上 |
| `src/orchestrator.py:1730/1738` | per-user 情绪状态 fallback 分支（`data/emotions/<uid>.json`） | P1 | 原子写（主路径 repo.save 已原子，fallback 裸写） |
| `src/orchestrator_hooks.py:61/64` | 同上（hook 路径） | P1 | 同上 |
| `src/runtime/personality_event_bus.py:565` | runtime 人格事件总线持久化（runtime_core 调用） | P1 | 原子写 + 锁 |
| `src/runtime/reflection_growth_bridge.py:385` | runtime 反思成长历史（runtime_core 调用） | P1 | 同上 |
| `src/runtime/adapters/growth_proposal_mirror.py:70/84` | GrowthProposal 镜像存储（governance_mirror_integration 调用） | P1 | 同上 |
| `src/runtime/adapters/growth_adapter.py:400` | growth adapter 历史（runtime_core/integration_host 调用） | P1 | 同上 |
| `src/runtime/lifecycle_manager.py:811` | runtime 生命周期历史（runtime_core 调用） | P1 | 同上 |
| `src/runtime/pipeline/runtime_growth_pipeline.py:683` | runtime 成长管线历史（orchestrator/runtime_pipeline 调用） | P1 | 同上 |

**建议修复（辅助/低频/非人格数据，本轮不动）**

`src/storage/yuyi_persistence.py:39`（写路径休眠，仅 legacy 读）、`src/permission/permission_manager.py:34/60`、`src/growth/approval_manager.py:555`、`src/growth/growth_limiter.py:157/196`、`src/growth/event_history_store.py:27`、`src/growth/sync/retry_queue.py:98/112` + `retry_worker.py:74/87`、`src/agreement/agreement_repository.py:33`、`src/admin/core/config_manager.py:143`、`src/admin/core/audit.py:139`（无锁 append，V1.0 R8）、`src/memory/memory_relevance_evaluator.py:224`、`src/emotion/emotion_manager.py:118`（分析计数器）、`src/understanding/pattern_learner.py:100`、`src/orchestrator.py:1438`（conversation_history 缓存，V1.0 已记 Low）。

**已原子（V1.0/V1.1 前复核通过，无需处理）**：`runtime_core.py` state_file（`_state_file_lock` + 原子写 + 损坏备份，:314-326）、`memory_store.py:476`、`growth_state.py`、`proposal/storage.py`、`proposal_review.py:150`、`emotion_repository.py:20`（tmp+fsync+replace）、`emotion_trace_repository.py:36`、`personality_state.py:349`（V1.0 R4）、`origin_storage.py`（V1.0 R5）、`relationship_repository.py`（V1.0 R1）、`context_storage.py:142`、`runtime_snapshot.py:557`、`response_phase4/persistence_manager.py:531`、`self_model_persistence.py`（tmp 模式）。

### B2. 路径不稳定复核（除 ProposalStorage 外，建议修复级）

以下存储默认路径为 cwd 相对，生产 cwd 稳定不触发、测试均已显式传 tmp 路径 → 建议修复（统一 `resolve()`）：`growth_state.py`（`data/growth_state.json`）、`emotion_repository.py`、`emotion_pattern_repository.py`、`personality_state.py`、`origin_storage.py`（`data/identity/origin_identity.json`，本仓库从未生成过该文件）、`agreement_repository.py`、`permission_manager.py`、`audit/storage.py`、`context_storage.py`、orchestrator 的 `data/emotions/<uid>.json` 与 conversation_history。本轮只修 ProposalStorage（Phase 1 方向 1 点名）。

### B3. 存量测试垃圾（必须修复，P2 测试污染）

| 对象 | 内容 |
|---|---|
| `data/emotions/test_user_p0.json` | 2026-08-06 测试残留 per-user 情绪文件 |
| `data/growth/test_growth_closed_loop_report.json` | 2026-08-06 测试报告 |
| `data/growth/test_selfmodel_persistence_report.json` | 2026-08-06 测试报告 |
| `data/growth/limiter/test_12_limiter.json`、`test_12_v2.json` | 测试限流器状态残留 |

`data/emotions/366648462.json`（真实用户）mtime 19:17 落在 V1.0 污染批次窗口内，内容为非默认演化值、来源无法判定（测试/真实）→ **不动**，记录为残余不确定性。

---

## C. Runtime

| 检查项 | 状态 |
|---|---|
| RuntimeBridge 单例 / initialize 幂等 / shutdown 语义 | ✅（V1.0 审计 + smoke 覆盖） |
| `runtime_state.json` 保存 | ✅ 原子写 + 锁 + 损坏备份（`:314-326`） |
| restart 恢复 / 状态一致性 | V1.0 smoke 12 项通过；V1.1 Phase 3 冒烟将做多轮循环验证 |
| 异常恢复 | `_start` 整体 try/except fail-soft ✅ |

---

## D. Test Engineering

### D1. 未隔离写测试池（30 个文件，必须修复：收紧隔离网 + 警告机制）

「含写操作 + 无 conftest 模式匹配 + 无 tmp_path/mkdtemp/chdir/标记自隔离」的文件：

`tests/manual/test_phase_3_7_6_live_validation.py`、`tests/runtime/test_phase381_semantic_consistency.py`、`test_admin_selfmodel_health_ui.py`、`test_admin_selfmodel_ui_integration.py`、`test_design_system.py`、`test_event_stream_provider.py`、`test_evolution_evaluator.py`、`test_experience_bridge_contract.py`、`test_full_system_e2e.py`、`test_health_check_desktop.py`、`test_identity_resolver.py`、`test_memory_system.py`、`test_p0_chat_completions.py`、`test_personality_evolution_bridge.py`、`test_personality_growth_record.py`、`test_phase40_r271a_offline_cognitive_loop_gates.py`、`test_phase40_r275_life_simulation_gates.py`、`test_phase_5_0_d1/test_event_emitter.py`、`test_phase_5_0_d2/test_integration_tasks_dispatch.py`、`test_phase_5_0_d3/test_self_model_integration.py`、`test_phase_5_0_d3_c/test_interest_signal.py`、`test_phase_5_0_d3_d/test_desire.py`、`test_phase_6_2_guardian.py`、`test_phase_6_3_sync_adapter.py`、`test_phase_d6_1_existence_adapters_timeline.py`、`test_phase_d6_2_1_integration.py`、`test_runtime_phase_b12.py`、`test_runtime_stage_contract.py`、`test_self_model.py`、`test_self_model_v2.py`。

处置：A3 访问器补入正则后重扫；剩余文件逐个甄别（自隔离/标记/补正则），记录结论。同时新增**会话级数据污染告警**（session 前后核心数据哈希比对，警告列出被改文件）作为兜底。

### D2. 真实 LLM 风险

- `tests/runtime/test_phase381_semantic_consistency.py`：4 个用例**明确使用真实 LLM API**（"需要 API key 有效"），未标记 real_api，历史全量跑中即失败（基线 test_01_ai_companion_10_runs）→ **必须修复：整模块 `real_api` 标记**（默认跳过）。
- `test_real_llm_switch.py`：MagicMock 全覆盖 ✅。
- `test_p0_chat_completions.py`：自述不依赖真实 key（urllib/socket 本地网关），✅。
- V1.0 护栏（`test_phase381_meaning.py` `_BlockedLLMClient`、conftest real_api skip）完好 ✅。

### D3. cwd 依赖

- V1.0 已解决：tmp cwd 预建 `data/growth/proposals`、`data/audit`；`keep_repo_cwd` 标记机制（c6c AST 测试已用）。
- 剩余：30 文件池中部分测试依赖仓库 cwd（config/AST/localhost），逐文件甄别时确认。

---

## 风险汇总与 Phase 1 修复映射

| # | 风险 | 等级 | Phase 1 处置 |
|---|---|---|---|
| F1 | ProposalStorage cwd 相对路径（单例 + 路径漂移） | P1 | resolve() 绝对化 + reset_for_testing + conftest 注册 + 新测试 |
| F2 | 12 处生产路径裸写（evolution/snapshot/narrative/emotion_pattern/per-user emotion fallback ×2/runtime ×6） | P0/P1 | 统一 atomic_write_json + get_path_lock + 损坏备份，小范围逐文件 |
| F3 | conftest 正则漏 `get_*` 访问器 → 30 文件池隔离空洞 | P2 | 正则补访问器 + 重扫 + 污染告警 + 残余逐文件甄别 |
| F4 | test_phase381_semantic_consistency 真实 LLM 未标记 | P2 | 整模块 real_api 标记 |
| F5 | data/emotions + data/growth 5 个测试残留文件 | P2 | 备份 + sha256 + 删除 + 记录 |
| F6 | 建议修复级裸写清单（约 14 处）+ 相对路径默认值 + YuyiPersistence 写路径休眠 | 建议 | 记录，本轮不动 |
| F7 | data/emotions/366648462.json 19:17 写入来源不明 | 记录 | 不动，报告注明 |
