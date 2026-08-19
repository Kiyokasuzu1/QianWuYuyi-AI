# Phase 4.1C 后处理 · 测试语义修复报告

> 日期：2026-08-12
> 范围：仅测试层。未修改 SelfModelEvolutionPolicy；未修改 RuntimeCore 及任何生产逻辑
> 运行环境：用户 Python 3.14.6 + pytest 9.1.1（项目完整依赖可用，本机直接跑真实套件）

---

## 1. 本轮改动（3 个测试文件，0 个生产文件）

### 1.1 新增 `tests/runtime/test_self_model_stage_chain.py`（19 个测试，全部通过）

把 4.1b 临时验证脚本（G1-G16，已删除）固化为正式测试，并按 4.1C 新语义更新三条：

| 测试 | 4.1b 旧语义 | 4.1C 新语义（本轮落地） |
|------|-------------|--------------------------|
| G2 | Stage 10 入队即设 `_self_model_changed` | 入队 **不** 设 changed；changed 只由 Stage 12 实际应用时设置（否则 deny 提案触发空落盘） |
| G9 | policy deny「不应用」 | deny 必须 **出队（reject）** 且 Stage 13 **零 save 调用**（永不落盘），拆为 G8（出队）+ G9（不落盘）两个断言 |
| G16 | 四段链标记齐全 | 额外要求 `validation.policy_decision` 为逐条决策字典（Dict[sid, decision]） |

新增 4.1C 验收测试：
- **G17**：`policy_engine=None` 时免审批提案全部 `held_no_policy` 挂起——不应用、不出队、不标记 changed（4.1b 宽松直批分支已移除，fail-safe 方向锁定）
- **G18**：policy=None 挂起状态跨轮稳定（连跑两轮不泄漏不丢失）
- **test_policy_injected_on_init**：`__init__` 必须装配真实 `SelfModelEvolutionPolicy`

其余 G 测试（入队/去重/链标记字段/reflection skip/allow 应用/审批永等/低置信挂起/字段映射/混合批次/落盘三分支）按现行语义保留。G11 专门锁定 4.1C 修复的字段映射 bug：`_suggestion_to_change_dicts` 输出必须落在策略字段集（EVOLVABLE ∪ PROTECTED）内，且 `add_core_values → fundamental_values`。

测试实现要点：真实 `RuntimeCore(config)`（与 test_self_model_system.py 同款配置）+ 真实策略；`get_self_model_full` patch 为 ≥10 顶层字段快照保证 change_ratio ≤ 0.20；Stage 13 用记录型假 adapter 只测接线；Stage 11 在本配置下 scheduler 未装配，赋真值哨兵使阶段体执行。

### 1.2 修复 `tests/runtime/test_identity_context_phase2.py`（1 处断言）

`test_identity_context_positioned_after_core_identity` 锚定的硬编码串
「你是浅雾羽依，一个通过系统机制持续演化的AI人格。」已被 P1（Phase 4.0.2 身份统一）
替换为 IDENTITY_CORE 驱动文本。断言锚点改为 `f"你是{IDENTITY_CORE['name']}。"`，
位置断言（core → 注入 → 核心原则）语义不变。**这是基线对比发现的回归**（P1 改动使旧断言过时）。

### 1.3 修复 `tests/runtime/test_life_cycle_closed_loop.py`（1 处配置）

`test_full_closed_loop` 中 `InitiativeBridge(send_config={...})` 未提供 `target_user`。
Phase 7.2 后的 bridge 要求明确目标用户（payload > send_config > orchestrator，
无目标直接跳过发送）。send_config 补 `"target_user": "user001"`（与测试用户一致），
闭环其余断言不变。**同为基线对比发现的回归**（bridge 语义变化未同步到此测试）。

## 2. 验收结果

```
pytest tests/runtime  →  144 passed, 4 failed
```

**4 个失败全部为既有（committed HEAD 基线）问题，与本轮及 4.1x 全部改动无关。**
验证方法：`git stash` 全部未提交改动后在 HEAD 基线跑同一套件对比——

| 失败测试 | 基线(HEAD) | 当前 | 结论 |
|----------|-----------|------|------|
| test_cognitive_loop_verifier | ❌ | ❌ | 既有（根因见 §3.1） |
| test_end_to_end_simulation | ❌ | ❌ | 既有（同 §3.1 根因） |
| test_identity_context_phase2::...fallback_when_engine_missing | ❌ | ❌ | 既有（§3.2） |
| test_relationship_intelligence_runtime | ❌ | ❌ | 既有（§3.3） |
| ~~test_identity_context_positioned_after_core_identity~~ | ✅ | ❌→✅ | **本轮修复**（P1 回归） |
| ~~test_full_closed_loop~~ | ✅ | ❌→✅ | **本轮修复**（7.2 回归） |
| 基线另有 17 个 initiative_standalone 失败 | ❌ | ✅ | 早前会话已修复 |

「pytest tests/runtime -x 通过」的严格验收**暂无法达成**：-x 会停在第一个既有失败上。
剩余 4 个失败全部需要生产层决策，超出本任务「不改变生产逻辑」约束。

## 3. 既有失败根因（需决策，未动）

### 3.1 ⚠️ PollutionGuard × MemoryAdapter 自相矛盾（影响生产记忆连续性）

- `src/runtime/adapters/memory_adapter.py:201` 把经验转为记忆时，内容以
  `[RuntimeExperience]` 前缀开头；
- `src/memory/pollution_guard.py:96` 的 INJECTION_PATTERNS 又**显式拒绝**含
  `[RuntimeExperience]` 的内容（`injection_detected`）。

两者同于 commit `ad20c6e`（2026-08-07）引入。**自该提交起，所有
experience→memory 写入在生产中被 100% 拒绝**——这正是「记忆连续性」的关键链路。
两个候选最小修复（需您拍板，本轮未动）：
  a) adapter 去掉字面量前缀（元数据 `type=runtime_experience` 已携带类型信息）；或
  b) guard 对 `type=runtime_experience` 的自家条目豁免该模式。

### 3.2 ResponseAdapterImpl fallback 行为变化

`ResponseAdapterImpl(response_engine=None, fallback_reply="兜底回复")` 不再返回
配置的 fallback_reply，而是返回人格化兜底文案。显式配置被忽略——需确认是有意
（人格连续性优先）还是 bug（配置应优先）。

### 3.3 record_relationship_interaction 返回 None

`rc.record_relationship_interaction(...)` 返回 None，测试期望非 None。
需进一步定位是语义变化（改为异步/条件记录）还是回归。

## 4. 约束核对

| 要求 | 落实 |
|------|------|
| 1. 不修改 SelfModelEvolutionPolicy | ✅ 未动 |
| 2. 不修改 RuntimeCore 业务逻辑 | ✅ 未动（0 生产文件） |
| 3. 只更新过时测试断言 | ✅ 3 个测试文件 |
| 4. 增加 policy=None 挂起行为测试 | ✅ G17/G18 |
| 5. 旧测试与新策略语义一致 | ✅ 144 passed；G2/G9/G16 语义已更新 |
| 无新增 Runtime/SelfModel 系统 | ✅ |
