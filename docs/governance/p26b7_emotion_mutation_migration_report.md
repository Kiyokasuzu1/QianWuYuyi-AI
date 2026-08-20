# P2.3-B.7 Emotion Mutation Boundary Migration — 完成报告

> 任务：P2.3-B.7（Emotion Mutation Boundary Migration）
> 日期：2026-08-20
> 分支：develop/v1.1（HEAD 225d040，基线快照 /tmp/p26b7/baseline.txt）
> 前序：B.6 审计（docs/governance/emotion_mutation_boundary_audit.md）→ 本迁移

---

## 1. 修改文件

| 文件 | 类型 | 变更内容 |
| --- | --- | --- |
| src/emotion/mutation_adapter.py | 新增 | EmotionMutationAdapter（Event→MutationRequest 转换层 + 裁决执行约定层），模块级开关 `_emotion_mutation_gateway_enabled=False`（默认关闭） |
| src/emotion/emotion_manager.py | 修改 | `process_event` 增加 flag 分支：False=旧路径逐行一致；True=按维度走 Gateway（ACCEPT→apply_delta+save 执行件 / REJECT·NEED_REVIEW·DEFER→不改状态）；新增 `_process_event_governed` / `_apply_accepted_emotion` / `_ensure_mutation_adapter` / `get_mutation_adapter`；新增 `mutation_adapter` 构造参数（默认 None，按需构造） |
| src/orchestrator.py | 修改 | E-03 修复：`_process_emotion_post` 中 `repo.filepath = per_user_file` 现场改写 → 显式 per-user `EmotionRepository(str(per_user_file))` 实例持久化（外部 API 不变，fallback 链保留） |
| src/runtime/runtime_core.py | 修改 | `_stage_03_emotion_update` 补 can_modify_emotion 身份门（`_emotion_update_allowed` helper）；safe_mode / maintenance_mode 原逻辑不变（仍由 `_control_blocked` 决定）；关系更新子步骤不受影响 |
| src/governance/checks/identity_check.py | 修改 | PRIVILEGED_ACTORS 追加 `emotion_system`（镜像 B.5 growth_system 语义：放行但 requires_audit=True 审计强制） |
| tests/test_emotion_mutation_gateway.py | 新增 | 8 项覆盖测试（详见 §6） |

**未修改（硬边界）**：emotion 数据格式（EmotionState/EmotionDelta/EmotionalTrace 均未动）；prompt 表达层（EmotionContextProvider/dominant/intensity 派生逻辑）；B.3 Governance 检查实现（五道检查规则冻结）；data/ 任何文件。

---

## 2. Emotion 旧路径变化

**默认路径（flag=False）——字节级等价：**

```
EmotionEvent → EmotionEngine.process → EmotionDelta
  → EmotionState.apply_delta（纯函数，返回新对象）
  → EmotionRepository.save（原子写）
  → EmotionMemoryBridge.bind → EmotionTraceRepository.append
  → Phase 7.2 cognitive hook（只读）
```

旧路径语句顺序与迁移前完全一致，返回形状 `{delta, state_before, state_after, trace}` 不变（无新增键）。治理层在 flag 关闭时零参与（不构造 adapter、不产生任何 Gateway 调用或审计写入）。

**治理路径（flag=True）——仅新代码路径，默认不激活：**

- `process_event` 不再直写：先经 `_process_event_governed`，按维度（6 维中 delta≠0 者）逐项生成 MutationRequest（target_path=`emotion.state.<dim>`）→ 逐项 route → 裁决后才允许进入执行件。
- 轨迹 append 与 cognitive hook 两种模式一致保留（dynamics 引擎依赖 `payload.get("trace")`；hook 仅在状态实际变更后发射）。
- 返回形状在治理模式下增加 `mutation` 键（mode/decisions/applied/outcomes），默认模式不受影响。

**语义变化点（治理模式下）：**

- 真实引擎事件（|delta| 0.09~0.35）超过 B.3 EvidenceCheck 单次事件上限 0.01 → 全部进入 NEED_REVIEW（pending proposal 槽位），不修改状态。这是 B.3 冻结规则的治理语义（大单事件增量需复核），非本迁移引入的规则变更；ACCEPT 仅对 |delta|≤0.01、confidence≥0.75、≥3 条去重证据、risk=low 的请求放行（由测试注入小 delta 评估器验证）。
- 每维度独立裁决：部分维度 ACCEPT、部分 REJECT/NEED_REVIEW 时按维度独立生效。

---

## 3. Gateway 接入路径

```
EmotionEvent（user/system/runtime 三源）
  ↓ EmotionManager.process_event（flag=True）
  ↓ EmotionMutationAdapter.from_emotion_event
  │   · target_domain="emotion"
  │   · target_path="emotion.state.<dimension>"
  │   · actor_identity="emotion_system"（B.3 IdentityCheck 白名单，审计强制）
  │   · evidence：事件描述/类型/来源/memory_id 确定性构造（禁止 LLM 推断单独成立）
  │   · context_snapshot 自动补齐 request_id/trace_id/mutation_id（审计链路键）
  ↓ MutationRequest（9 字段冻结契约，JSON 安全校验禁止携带写句柄）
  ↓ MutationGateway.evaluate（CHECK_ORDER 冻结：identity→boundary→evidence→conflict→audit）
  ↓ MutationDecision（四态）
  ├─ ACCEPT     → _apply_accepted_emotion（原执行件：apply_delta + repository.save）
  ├─ REJECT     → 不修改状态（审计留痕仍生成）
  ├─ NEED_REVIEW→ adapter.pending_proposals 槽位（内存、带治理链接键、不落盘）
  └─ DEFER      → adapter.deferred 槽位（manager 层可消费重提）
  ↓ 每次 route 生成 envelope：request_id / mutation_id / trace_id / decision /
    audit_reference / applied / evidence_refs（审计四要素齐全，禁止无审计状态变化）
```

**审计接线**：Adapter 默认构造 `MutationGateway()` + `InMemoryAuditWriter()` 并注入 gateway.audit_writer；`record_request` 在评估前、`record_decision` 在决策定型后各调用一次，audit_reference 由 Gateway 回填。拒绝与失败同样留痕。MutationJournal/AuditTrail 生产落账（B.1 遗留 B11）不属于本迁移范围，仍由 RuntimeContext v2 mutations journal 投影预留（B.3 接口边界）。

**runtime / orchestrator 接线**：

- runtime `_stage_03_emotion_update`：身份门失败 → 情绪分支整体跳过（关系更新仍执行）。uid 回退链 ctx.user_id → config memory.target_user_id；两者均缺失保持旧行为（放行+日志）。
- orchestrator pre（`_process_emotion_pre`）：P2.1.3 门之后统一经 `em_manager.process_event` —— flag 开启时自动进入治理入口（单点接线，无需改动 orchestrator 逻辑）。
- orchestrator post：只持久化当前状态（不产生 mutation），E-03 修复后走 per-user 实例。
- legacy adapter（emotion_adapter_impl.analyze）：调用 `process_event` → flag 开启时自动经治理入口（由管理器单点保证，adapter 本体未改）。

---

## 4. B.6 债务关闭情况

B.6 注册表 E-01..E-11（docs/governance/emotion_mutation_boundary_audit.md）：

| 债务 | 本迁移状态 |
| --- | --- |
| E-03 filepath 现场改写（orchestrator `repo.filepath = per_user_file`，P0） | ✅ 已关闭：per-user EmotionRepository 实例，共享 repo 不再被改写（测试 #6 断言实例与 filepath 不变） |
| C2 runtime stage_03 无身份门（audit 黑洞） | ✅ 已关闭：can_modify_emotion 身份门 + uid 回退链（测试 #7 覆盖 user/sandbox/config 回退/空 uid 四态） |
| 主链 emotion 直写无 Gateway 治理（C1/C3/C6 链） | ✅ 已接入：EmotionManager.process_event 治理分支（flag=True 生效）；C1（orchestrator pre）、C3（legacy adapter）、C6（dynamics 引擎委托）均经此单点自动接管 |
| MutationJournal 无生产写入（B.1 遗留 B11） | ⏸ 部分推进：AuditWriter（InMemoryAuditWriter）已接线并回填 audit_reference；MutationJournal/AuditTrail 数据结构仍冻结未触碰（B.3 边界），生产落账留待 runtime mutations journal 投影 |
| 情绪永久化禁止 | ✅ 既有 BoundaryCheck 红线（permanent=True→REJECT）保留生效；本迁移未引入任何永久化路径 |
| 死链清理（EmotionPatternRepository / EmotionGrowthService / EmotionSelfModelBridge / orchestrator_hooks 模块级函数 / runtime record_emotion_event，C 类） | ⏸ 未清理：属 B.6 推荐顺序第⑦步 C 类清理，任务书未授权删除旧 API；保留并记录（见 §5） |

**B.6 推荐实施顺序对照**：① filepath 修复 ✅ → ② stage_03 gate+audit ✅（gate 已补；stage_03 无 MutationJournal 写入） → ③ emotion mutation adapter ✅ → ④ process_event gateway branch ✅ → ⑤ 三条链接线 ✅ → ⑥ MutationJournal wiring ⏸（AuditWriter 层完成） → ⑦ C 类清理 ⏸（未授权删除）。

---

## 5. 未迁移遗留入口

以下写入口未进入治理链，均为"删除类"清理项或低风险遗留（B.6 分类 C/A），任务书"不删除旧 API"原则下保留：

1. **runtime_core.record_emotion_event**（E-04，C5 链）：零调用方死代码，保留。
2. **orchestrator_hooks 模块级函数**（E-05，C4 链）：构造参数错误（content/timestamp 非 EmotionEvent 字段）导致 TypeError 被吞、零调用方，保留。
3. **emotion_adapter_impl.analyze**（E-06，C3 链）：legacy 链，经 `process_event` 单点已被治理分支覆盖（flag=True 生效），本体未改。
4. **runtime_core.get_emotion_manager 懒加载默认构造**（E-07）：默认构造的 EmotionManager 在 flag=True 时同样经治理分支；flag=False 保持旧行为。
5. **EmotionManager.update() 时间衰减**（E-08）：直接 apply_delta+save，未过 Gateway —— 衰减属 B.6 明确的"允许自动变化"类别（transient decay，无永久化），保持旧路径。
6. **增量计数 / 轨迹 append**（analysis_counter、trace append）：属审计/计数类 append，非状态 mutation，未纳入治理（B.6 分类 A）。
7. **死链三件套**（EmotionPatternRepository / EmotionGrowthService / EmotionSelfModelBridge，E-09..E-11）：零调用方，保留待 C 类清理。
8. **LLM 输出**：B.6 已确认 prompt 注入只读，LLM 输出不写 emotion（无需迁移）。

---

## 6. 测试结果

**新增测试**：tests/test_emotion_mutation_gateway.py — 10/10 PASSED（8 项覆盖 + 2 项 stage_03 附加断言拆分为 10 个用例）。

1. MutationRequest 字段正确（domain/path/actor/写句柄拒绝/跨域前置守卫）✅
2. flag 关闭旧行为一致（状态更新+持久化+旧返回形状，治理零参与）✅
3. flag 开启 ACCEPT 修改成功（apply 执行件 + 审计四要素）✅
4. REJECT 不改变状态（执行件不被调用，拒绝留痕）✅
5. NEED_REVIEW 不修改状态（pending proposal 入槽位带治理链接键，轨迹仍追加）✅
6. filepath 隔离（post 持久化不改写共享 repo 实例/filepath）✅
7. runtime stage_03 权限门（user 放行 / sandbox 拒绝 / config 回退 / 空 uid 旧行为）+ 阶段级跳过/放行验证 ✅
8. audit 记录（request+decision 落账，audit_reference 回填）✅

**A/B 回归**（电池：28 基础文件 = emotion 13 + runtime stage/lifecycle 8 + cognitive/response/chat/e2e/initiative 5 + governance/mutation 3；B 轮 + 新测试文件）：

| 轮次 | 代码状态 | 结果 |
| --- | --- | --- |
| A（迁移前） | 移除 adapter+gateway 分支+E-03 修复+stage_03 门+emotion_system | 11 failed / 606 passed / 1 skipped（28 文件，7.32s） |
| B（迁移后） | 当前代码 | 11 failed / 616 passed / 1 skipped（29 文件，7.53s） |
| 新增失败（B_FAILED − A_FAILED） | | **0**（FAILED 集合 sorted diff 为空）✅ |

- B 轮多出的 +10 passed = 新增测试文件 tests/test_emotion_mutation_gateway.py（10/10）。
- A/B 两侧 11 个失败为**既有失败**（与 B.7 无关，A 状态无任何 B.7 代码仍复现）：
  - 2× test_growth_mutation_gateway（API key 环境污染：tests/test_full_chat_lifecycle.py:70 模块级 `os.environ.setdefault("DEEPSEEK_API_KEY", "")` 使 load_dotenv 无法回填 → LLMClient ValueError；单跑该文件 11/11 通过，隔离复现验证）
  - 7× test_phase_3_8_4_response_integration（同类 API key 环境依赖）
  - 1× test_phase_4_0_1_lifecycle_executor::test_missing_stage_method_is_noop（既有断言与实现不一致：execute() 返回新 RuntimeContext 而非入参 ctx；隔离单跑即失败）
  - 1× test_runtime_full_lifecycle_verification::test_20_orchestrator_health_check（隔离单跑即失败）
- data/ 洁净度：`git status --short -- data/` 输出为空 —— A/B 两轮均零修改 ✅

---

## 7. 回滚方案

全部变更可逆、粒度独立：

1. **整体回滚（一键）**：备份副本位于 /tmp/p26b7/backup/*.b7（6 个文件，A/B 期间已验证 byte-exact 恢复流程）。恢复命令：
   - `cp /tmp/p26b7/backup/{emotion_manager,mutation_adapter,orchestrator,runtime_core,identity_check}.py.b7 <对应 src/ 路径>`（注意 backup 文件需去 .b7 后缀）
   - `cp /tmp/p26b7/backup/test_emotion_mutation_gateway.py.b7 tests/test_emotion_mutation_gateway.py` 或直接删除测试文件
2. **行为回滚（不删代码）**：flag 默认 False —— 生产行为与迁移前完全一致；无需改动任何代码即可停用治理路径（set_emotion_mutation_gateway_enabled 仅测试/灰度显式调用）。
3. **粒度回滚**：
   - 仅 E-03：恢复 orchestrator 三行（`repo.filepath = per_user_file`），无其他依赖。
   - 仅 stage_03 门：删除 `_emotion_update_allowed` + `gate_ok` 两处引用，恢复 `if em is not None:`。
   - 仅白名单：identity_check.py 移除 `emotion_system`（单行 frozenset 成员），不影响 B.5 growth_system。
   - 仅 adapter：删除 src/emotion/mutation_adapter.py + 移除 emotion_manager 的 `mutation_adapter` 参数与治理分支（flag 恒 False 时分支不可达）。
4. **数据格式**：本迁移未修改 EmotionState/EmotionDelta/EmotionalTrace 结构与 data/ 任何文件，无数据迁移或回滚需求。
5. **审计痕迹**：InMemoryAuditWriter 仅内存驻留（进程退出即失），不产生磁盘残留；data/ 零修改（A/B 轮验证）。

---

## 附：治理原则核对（AGENTS.md）

- Priority 1（身份连续性）：未触碰 IDENTITY_CORE；emotion 变更不永久化（BoundaryCheck 红线保留）✅
- Priority 3（事件→理解→评估→Proposal→审核→应用）：治理路径 = EmotionEvent（事件）→ EmotionEngine 评估 → MutationRequest → Gateway 五道审核 → ACCEPT 才应用 ✅
- Priority 4（保留旧接口/适配层/小修改）：flag 默认关闭、旧 API 全保留、新增仅为适配层 ✅
- 单一职责：Adapter 只转换+约定；Gateway 只决策；执行件仍在 EmotionManager ✅
