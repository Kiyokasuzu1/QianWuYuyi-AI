# P2.6 Phase C-0：SelfModel Approved Drain 详细设计（冻结）

> 性质：只读设计文档（未修改代码、未新增模块、未修改 RuntimeCore、未开启 flag；仅新增本文档）
> 日期：2026-08-21
> 上游：p2_6_phase_b1_freeze_baseline.md（U1 缺口）→ p2_6_phase_b_validation_review.md §8（Phase C 前置条件）
> 下游：Phase C-1 实施（需单独任务书授权行级白名单）

---

## 0. TL;DR 设计裁决

1. **新建独立 consumer 模块**（不复用 personality drain、不扩展现有 drain 方法体）。
2. **接入点 = orchestrator**：init 兜底一次 + Step 14.6 之后每轮循环消费。**RuntimeCore 零修改**（满足本任务红线；备选 stage13 方案仅登记不冻结）。
3. **already-applied 双保险**：`status == APPLIED` 跳过 + `source.growth_id` 对 `growth_narratives[].record_id` 幂等去重——**无需改造 SelfModelStore**。
4. **apply 成功判定不走返回值**（store/updater 均吞噬异常）：apply 后查 `record_id` 是否出现在 narratives 中。
5. **identity 隔离已成立**：apply 只追加叙事 + 微调 self_understanding，不触碰 identity_core / PersonalityState / relationship / memory（证据见 §4）。
6. **Go**：Phase C-1 允许实施，前置条件 4 条（§7）。

---

## 1. self_model 数据模型与生命周期

### 1.1 SelfModelChangeProposal（src/personality/self_model_updater.py:52）

| 字段 | 类型 | 说明 |
|------|------|------|
| change_type | str | `narrative_append` \| `self_understanding_update` |
| target | str | `growth_narratives` / `self_understanding` |
| change | dict | narrative/dimension/growth_level/growth_signal/event/meaning |
| source | dict | growth_id(=record_id)/source_event_id/source_type/affected_dimensions/evidence_ids/confidence |
| timestamp | str | ISO |
| suggestion_id | str | 默认 `sug_<hex>` |
| requires_approval | bool | 默认 True |

**载荷格式**：Phase B-1 的 `_persist_self_model_governance_proposal`（orchestrator.py:2255）把 `proposal.to_dict()` 存入 B-store 提案的 `metadata["self_model_proposal"]`，与上述字段一一对应。**反序列化 = `SelfModelChangeProposal(**payload)`**——字段名完全匹配，缺省字段由 dataclass 默认值兜底（该类无 from_dict，但无需新增）。

### 1.2 SelfModelUpdater.apply_proposal 输入要求（self_model_updater.py:228）

- 输入：`SelfModelChangeProposal` 实例（已批准）。
- 行为：`_apply_to_store` → `store.apply_change_proposal(proposal)` → 追加到内存 `_applied_proposals`（仅会话内）。
- **关键约束**：`_apply_to_store` 内 try/except 吞噬异常（只 log warning），`apply_proposal` 恒返回 proposal——**无成败信号**。消费器不能依赖返回值判成败（见 §2.5）。

### 1.3 SelfModelStore 写入方式（src/personality/self_model_store.py:441）

- `apply_change_proposal(proposal)`：增量追加（不重建模型）；空 store 自动建基础模型；成功后 `_save_to_disk()`（原子写 + per-path RLock）+ live-sync 到 Manager。**返回 None，异常同样被吞**。
- 写入内容仅限：`growth_narratives` 追加（上限 20 条，含 record_id/dimension/narrative/meaning/timestamp/_source_growth_id/_confidence 追溯字段）+ `self_understanding` 四指标微调（每次 ≤ +0.05）+ `last_updated`。
- **无 applied-proposal 记账**（personality 的 `has_proposal_been_applied` 账本在 PersonalityState，self_model 侧不存在等价物）——因此设计幂等去重（§2.4）。

### 1.4 applied 状态回写字段（镜像 personality drain 契约）

| 字段 | 值 |
|------|----|
| status | `APPLIED` |
| applied_by | `"self_model_drain"` |
| applied_at | ISO UTC |
| metadata | 保留原有（last_review / governance_decision / self_model_proposal 不动），可追加 `{"drain_applied_at": iso, "drain_result": {...}}` |

### 1.5 生命周期图

```
Step 14.6 (B 态)                    admin governance_provider.review
  _persist_self_model_governance_     (governance_provider.py:757, 无类型过滤)
  proposal → B-store pending ────────→ B-store approved
                                           │
                    ┌──────────────────────┘  每轮 Step 14.6 之后（+ init 兜底）
                    ▼
             drain candidate            list_by_status(APPROVED, limit)
             过滤 proposal_type == self_model（消费器内过滤）
                    │
                    ▼
               validate              ① change_type 白名单 ② 影响域检查（§4.3）
                    │                ③ 幂等去重（record_id 已存在 → skip）
                    ▼
                apply                store.apply_change_proposal(reconstructed)
                    │
                    ▼
               verify                store.get()["growth_narratives"] 中
                    │                record_id == source.growth_id 出现？
            ┌───────┴────────┐
           yes              no
            ▼                ▼
        applied           failed（保持 approved，下轮/下次启动重试）
      status=APPLIED       审计 self_model_drain_failed
      审计 apply_success
```

---

## 2. self_model approved drain 设计（消费者）

### 2.1 是否新建独立 consumer / 是否复用 personality drain

**新建独立 consumer**（新模块，如 `src/growth/self_model_approved_drain.py`，纯函数 + 结果字典）。**不复用 personality drain**，理由：

1. 红线：personality drain 方法体（runtime_core.py:6390-6524）语义冻结，扩展即违反。
2. 共享 config 开关风险：现有 `growth_apply_drain_enabled` 默认 **true**——若 self_model 消费挂同一开关，实验期误开即捆绑生效。
3. 数据语义不同：personality 走 PersonalityEvolutionPipeline（含身份连续性检查）；self_model 走 store 增量追加，需独立 validate。
4. 失败语义不同：personality 依赖 PersonalityState 账本判 already-applied；self_model 无账本，用幂等去重。

### 2.2 proposal_type 过滤位置

**消费器内部过滤**（不改 drain、不改 storage）：`getattr(p, "proposal_type", "") != PROPOSAL_TYPE["SELF_MODEL"] → skip`。全仓只有 drain（personality）与本消费器（self_model）两个 approved 消费者，互不重叠。

### 2.3 limit 机制

- config 键 `self_model_drain_limit`，默认 3（独立于 personality 的 5）。
- `list_by_status(APPROVED, limit=_limit)` 由 storage 现有接口承担；单轮处理上限，剩余下轮继续。

### 2.4 already-applied 判断（双保险，零 store 改造）

1. `proposal.status == APPLIED` → skip（防御性，正常 list 不会返回）。
2. **幂等去重**：重构后 `proposal.source["growth_id"]`（= GrowthRecord.record_id）与 `store.get()["growth_narratives"][*]["record_id"]` 比对——已存在 → `already_applied` skip。这同时解决「apply 成功但状态回写失败 → 重试重复追加」的经典窗口。

### 2.5 apply 与成功判定（fail-soft 契约）

- 每 proposal 独立 try/except；任何异常 → `failed` 计数 + 保持 approved（下轮/下次启动自动重试），绝不影响聊天主链。
- 因为 store/updater 都吞噬异常（§1.2/§1.3），**成功判定 = 事后校验**：apply 后重读 `store.get()`，`record_id` 出现 → success（回写 APPLIED）；未出现 → failed（保持 approved，不写 applied）。
- config `self_model_drain_enabled` 默认 **False**；关闭时返回 `{"enabled": false, "reason": "disabled_by_config"}`。

### 2.6 异常恢复

| 场景 | 行为 |
|------|------|
| 消费器整体异常 | 调用点 try/except 隔离（orchestrator 既有模式），本轮跳过 |
| 单 proposal apply 失败 | 保持 approved，下轮重试 |
| apply 成功但回写失败 | 幂等去重拦截重复；下轮补回写 |
| 载荷损坏/缺 metadata | validate 拒绝 → failed（不回写不 apply），审计留痕，人工清理 |
| 进程重启期间 admin 批准 | init 兜底 drain 一次（orchestrator 初始化处） |

---

## 3. RuntimeCore 接入点设计

### 3.1 现状

- stage13（runtime_core.py:6341）每轮：先 personality drain（6355，fail-soft）→ `_self_model_changed` 持久化（save_state）。
- startup drain（runtime_core.py:1287）：停机期间批准的 personality 提案兜底。
- 二者只覆盖 personality；self_model 无对应点。

### 3.2 方案比较

| 方案 | 侵入 | 风险 | 回滚 | 兼容性 |
|------|------|------|------|--------|
| A：新增调用点（stage13 或 orchestrator） | 1 行调用 + 独立模块 | 低 | 删 1 行 | 高 |
| B：扩展现有 drain | 改方法体 | **中：违反「personality drain 语义不变」红线 + 共享开关捆绑** | 需还原方法体 | 中 |
| C：独立 background consumer | 新线程/调度件 | **高：并发写 store（内存态竞争，锁只护磁盘）+ 新架构件违背闭环优先** | 删模块 | 低 |

**裁决：A 变体——orchestrator 侧调用，RuntimeCore 零修改。**

- 调用点 1（loop drain）：orchestrator Step 14.6 之后，`self._drain_approved_self_model_proposals()`（fail-soft，每轮聊天即消费）。
- 调用点 2（startup 兜底）：orchestrator 初始化处调用一次。
- 选 orchestrator 而非 stage13 的依据：① 本任务明令禁止改 RuntimeCore；② 提案生产方（Step 14.6）与消费方同进程同生命周期，store 实例即 updater 的实例，无跨实例/跨进程分歧；③ RuntimeCore 主链 stage 契约红线不动。
- 备选（登记不冻结）：若未来任务书授权 RuntimeCore 行级修改，同一消费器模块加 stage13 一行调用即可，双调用点天然幂等（record_id 去重），无冲突。
- 注意：orchestrator.py 属 Phase B-1 冻结文件——**Phase C-1 任务书需明确授予「orchestrator 两处调用点」行级豁免**（冻结基线 §5 的唯一修订项，需在 C-1 文档声明）。

---

## 4. identity continuity 检查设计

### 4.1 影响域分析

| 对象 | 是否受影响 | 隔离依据 |
|------|-----------|----------|
| identity_core | ❌ 不影响 | 独立模块/独立存储；store 写路径不含 identity 字段（基础模型中的 identity_name 为 store 自有常量，非 identity_core 数据） |
| personality_state | ❌ 不影响 | 独立文件（personality_state.json）；SelfModelUpdater RULE 1「不修改 PersonalityState」；apply 只追加 narratives + self_understanding |
| relationship | ❌ 不影响 | 独立模块，无调用关系 |
| memory | ❌ 不影响 | 独立模块，无调用关系 |

隔离结论：**无需新增保护机制**。self_model 更新在数据面与身份/人格/关系/记忆完全隔离，唯一写入 = SelfModelStore 文件（growth_narratives ≤20 条 + understanding 四指标 ≤+0.05/次）。且已有双重治理前置：policy evaluate（提案生成时）+ admin approve（B-store approved）——消费器是第三道（apply 执行），不引入新决策。

### 4.2 需要保留的既有保护（消费器不得绕过）

- 消费器只处理 `status == APPROVED`（admin 显式批准）的提案。
- 消费器不调用 governance policy evaluate（决策在提案时已固化于 metadata["governance_decision"]），避免二次裁决漂移。

### 4.3 轻量 validate（apply 前，纯防御）

1. `change_type ∈ {narrative_append, self_understanding_update}`，否则 rejected。
2. 载荷字段域检查：`change` 中不允许出现 `current_traits`/`stable_traits`/`identity_*` 键（防未来 payload 注入越域）。
3. 幂等去重（§2.4）。

---

## 5. 审计指标设计

### 5.1 指标定义（消费器 result 字典 + 审计记录）

| 指标 | 定义 | 计数位置 |
|------|------|----------|
| self_model_drain_processed | 本轮扫描的 approved self_model 候选数 | 过滤后计数 |
| self_model_apply_success | 校验通过 + store 校验确认写成功的提案数 | apply verify 通过 |
| self_model_apply_rejected | validate 拒绝（类型/越域/载荷损坏）数 | validate 阶段 |
| self_model_drain_failed | apply/verify/回写异常数 | except 分支 |

落点：orchestrator print（镜像 Phase 4.0.3 风格）+ 既有 `record_audit_log`（operation_type=`self_model.proposal_applied`，actor=`self_model_drain`，detail 含 proposal_id/指标）。Phase C-1 可选扩展：写入 Phase A 治理探针 JSONL（probe 扩展，非本设计冻结项）。

### 5.2 approved legitimate apply vs legacy direct apply 区分矩阵

| 路径 | 标记 | 判定 |
|------|------|------|
| B 态消费器 apply（本设计） | 审计 actor=`self_model_drain` + B-store status=APPLIED 回写 | **legitimate** |
| A 态 Step 14.6 auto_apply（legacy） | 治理决策 metadata action=auto_apply + 无 B-store 提案 | legacy（A 态保留，flag=False 默认） |
| runtime_core.py:2072 | 无审计（dormant） | legacy（不可达；Phase C-1 顺手门控） |
| accept_self_model_suggestion | 建议域显式批准（queue.approve） | 独立域，非提案链 |

**B 态不变量**：self_model store 变化 ⇔ 存在 actor=`self_model_drain` 审计（或建议域审计）；否则视为异常写入，触发告警（设计为监控指标，非强制拦截）。

---

## 6. Phase C-1 实施边界预览（非本任务授权，仅登记）

- 新增：`src/growth/self_model_approved_drain.py`（消费器）+ 单测 + subprocess 端到端快照测试 + 实施文档。
- 行级豁免：orchestrator.py 两处调用点（init 兜底 + Step 14.6 后）。
- 配置：config.yaml 新增 `self_model_drain_enabled: false` + `self_model_drain_limit: 3`。
- 禁止：RuntimeCore 全部方法体、personality drain、Stage 04、policy RULES、self_model_store.py、self_model_updater.py（只读复用）、双体系 schema。
- 声明边界：admin MODIFY 审查的 trait 合并逻辑面向 personality 形状；self_model 提案 Phase C 仅支持 approve/reject。

---

## 7. 最终裁决：**Go**（Phase C-1 允许实施）

前置条件（C-1 任务书必须包含）：

1. 消费器默认关闭（`self_model_drain_enabled: false`），开启仅限测试/实验环境。
2. orchestrator 两处调用点的行级豁免明确写入 C-1 任务书（冻结基线 §5 修订声明）。
3. personality drain 方法体零改动；RuntimeCore 本阶段零改动。
4. 验收必须含：单测（validate/幂等/fail-soft/limit/回写）+ isolated subprocess 端到端（pending→approve→drain→self_model hash 变 + personality/identity 文件 hash 不变 + 提案转 applied）+ 基线回归（45 快电池 + 2 真实链单测保持通过）。
