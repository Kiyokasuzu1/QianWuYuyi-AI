# Growth Proposal Review Process — Phase C.4.6.3

> **Phase:** C.4.6.3 — Growth Proposal Review Workflow
> **生成时间:** 2026-08-02
> **状态:** ✅ **ACTIVE** (强制人工 review)
> **核心原则:** **禁止 auto_accept**

---

## 0. 目标

为 Growth Proposal 建立 **完整、可审计、可回滚** 的人工 review 流程,
防止自动应用导致 CoreIdentity / Personality 异常变更。

**关键不变量:**

- ❌ **禁止** `auto_accept_enabled=True`
- ✅ **必须** 人工 review 每个 proposal
- ✅ **必须** 记录 reviewer_id
- ✅ **必须** 记录 review_comment(reject 时强制)
- ✅ **必须** 审计日志完整

---

## 1. Proposal 生命周期

```
┌─────────────────┐
│  Memory Event   │  用户对话 → memory.json (role=user)
└────────┬────────┘
         ↓
┌─────────────────┐
│ GrowthEvaluator │  评估事件,生成 growth_level
└────────┬────────┘
         ↓
┌─────────────────┐
│ GrowthProposal  │  pending → 持久化到 data/growth/proposals/proposals.json
└────────┬────────┘
         ↓
┌─────────────────┐
│  Human Review   │  ⏸️ 等待人工决策
└────────┬────────┘
         ↓
   ┌─────┼─────┬──────────┐
   ↓     ↓     ↓          ↓
┌─────┐ ┌─────┐ ┌─────┐ ┌──────────┐
│APPR.│ │REJ. │ │ARCH.│ │  EXPIRE  │
│应用  │ │拒绝 │ │归档 │ │  超时   │
└──┬──┘ └─────┘ └─────┘ └──────────┘
   ↓
┌─────────────────┐
│  SelfModel      │  approve 后触发 beliefs/history/reflection/relationship 写入
│     Update      │  (data/self_model/*.jsonl)
└─────────────────┘
```

---

## 2. 各阶段详细说明

### 2.1 Memory Event(输入)

**位置:** `data/memory.json` (`role=user` 记录)

**触发条件:**

- 用户发送对话(LLM 调用前/后)
- 事件重要性 > threshold(由 `GrowthEvaluator` 决定)

**示例:**

```json
{
  "memory_id": "mem_...",
  "role": "user",
  "content": "我喜欢晚上看书",
  "memory_type": "user_preference",
  "timestamp": "2026-08-02T..."
}
```

### 2.2 GrowthEvaluator(评估)

**位置:** `src/growth/growth_evaluator.py`

**职责:**

- 评估 memory event 的成长潜力
- 输出 `growth_level` ∈ {`none`, `trace`, `context`, `preference`, `relationship`}
- 若 ≥ `preference`,触发 proposal 创建

**关键不变量:**

- 不修改 CoreIdentity
- 不写入 SelfModel(只生成 proposal)
- 所有评估基于 evidence,无证据拒绝

### 2.3 GrowthProposal(创建)

**位置:** `data/growth/proposals/proposals.json`

**Schema(v1.0 frozen):**

```json
{
  "proposal_id": "prop_...",
  "proposal_type": "preference",
  "status": "pending",
  "source": "growth_evaluator",
  "source_event_id": "evt_...",
  "user_id": "user_...",
  "affected_dimensions": ["reading_habit"],
  "before_state": {},
  "after_state": {"reading_habit": 0.7},
  "confidence": 0.85,
  "reason": "用户在3次对话中表达相同偏好",
  "evidence": [
    {"text": "我喜欢晚上看书", "role": "user", "source_index": 0}
  ],
  "priority": "normal",
  "created_at": "2026-08-02T..."
}
```

**状态机:**

```
pending  ──approve──→  approved  ──apply──→  applied
   │
   ├──reject────→  rejected
   │
   └──archive──→  archived
```

### 2.4 Human Review(强制人工)

**工具:** `src/growth/proposal_review.py` (Phase C.4.3)

**核心命令:**

```bash
# 1. 列出 pending
python src/growth/proposal_review.py list --status pending

# 2. 查看详情(包含 evidence + confidence)
python src/growth/proposal_review.py show <proposal_id>

# 3. 决策
python src/growth/proposal_review.py approve <proposal_id> \
    --reviewer <reviewer_name> \
    --comment "<review comment>"

python src/growth/proposal_review.py reject <proposal_id> \
    --reviewer <reviewer_name> \
    --comment "<reject reason>"   # reject 必须有 comment

python src/growth/proposal_review.py archive <proposal_id> \
    --reviewer <reviewer_name> \
    --comment "<archive reason>"

# 4. 摘要
python src/growth/proposal_review.py summary
```

**Reviewer 必填字段:**

| 字段 | approve | reject | archive |
| --- | --- | --- | --- |
| `reviewer` | ✅ 必填 | ✅ 必填 | ✅ 必填 |
| `comment` | 推荐 | **必填** | 推荐 |

### 2.5 Accept / Reject / Archive(决策)

#### Approve(批准)

**效果:**

- `proposal.status` → `approved`
- 记录 `reviewer_id` / `review_comment` / `reviewed_at`
- **触发** SelfModel update(写入 beliefs / history / relationship)

**回滚:** 不允许(apply 后不可回滚 — Phase C.4.6 限制)

#### Reject(拒绝)

**效果:**

- `proposal.status` → `rejected`
- 记录拒绝原因(comment 必填)
- **不**触发 SelfModel update

**回滚:** 可重新 review(将 status 改回 pending)

#### Archive(归档)

**效果:**

- `proposal.status` → `archived`
- 适用于已过期 / 不再相关 / 重复的 proposal
- **不**触发 SelfModel update

**回滚:** 可重新 review

### 2.6 SelfModel Update(应用)

**位置:** `data/self_model/{beliefs,history,reflection,relationship}.jsonl`

**触发条件:** proposal status 变为 `approved`

**写入内容:**

| 字段 | 写入文件 |
| --- | --- |
| `affected_dimensions` / `after_state` | `history.jsonl` |
| 抽象 belief | `beliefs.jsonl` |
| 用户关系变化 | `relationship.jsonl` |
| 反思记录 | `reflection.jsonl` |

**关键约束:**

- ❌ 不写入 CoreIdentity 相关字段
- ❌ 不修改 `forbidden_changes` 列表中的任何字段
- ✅ 写入需经过 `CoreIdentity.safety_check()`

---

## 3. 风险与控制

### 3.1 风险表

| 风险 | 严重度 | 当前缓解 | 备注 |
| --- | --- | --- | --- |
| 批量 auto-accept | **CRITICAL** | `auto_accept_enabled=False` 强制 | C.4.3 工具仅暴露人工接口 |
| 误 apply 到 CoreIdentity | **CRITICAL** | `CoreIdentity.safety_check()` 拒绝 | Growth 写 SelfModel 前必过此检查 |
| Pending 队列累积 | MEDIUM | C.4.3 review 工具 | 当前 182 条,需 review |
| Reviewer 身份不明 | LOW | `--reviewer` 必填 | 禁止空字符串 |
| Comment 缺失 | LOW | reject 必填 comment | 其他决策推荐填写 |

### 3.2 控制点

1. **代码层:** `proposal_review.py` 不调用 `PersonalityAdapter`
2. **配置层:** `auto_accept_enabled=False`(永不可改)
3. **数据层:** review 字段强制完整(reviewer / comment / reviewed_at)
4. **审计层:** 所有 review 操作记录到 `.cache/audit/proposal_review_log.jsonl`
5. **监控层:** `runtime_health_monitor.py` 检测 proposal pending 累积

---

## 4. 拒绝(Reject)理由标准

### 4.1 应 reject 的情况

- ❌ 证据不足(evidence < 1)
- ❌ confidence < 0.6
- ❌ 与已有 memory 重复
- ❌ 影响 CoreIdentity 关键词
- ❌ 危险描述(出现"冷漠"/"攻击性"/"完全改变人格"等)
- ❌ 单次事件即触发的 relationship 类 proposal

### 4.2 应 archive 的情况

- ❌ 创建时间 > 30 天的 pending proposal
- ❌ 用户已变更相关偏好
- ❌ 重复 proposal(同 source_event_id)

### 4.3 应 approve 的情况

- ✅ evidence ≥ 3 条用户明确表达
- ✅ confidence ≥ 0.8
- ✅ 描述清晰,影响范围明确
- ✅ 不涉及 CoreIdentity / Personality 核心字段
- ✅ proposal_type ∈ {`preference`, `memory_growth`}(谨慎处理 `relationship`)

---

## 5. 工作流示例

### 5.1 每日 Review 流程

```bash
# Step 1: 列出 pending
python src/growth/proposal_review.py list --status pending

# Step 2: 按 proposal_id 逐条查看
python src/growth/proposal_review.py show prop_001
# → 查看 evidence / confidence / affected_dimensions

# Step 3: 决策(示例:reject)
python src/growth/proposal_review.py reject prop_001 \
    --reviewer human_operator \
    --comment "证据仅 1 条,不足以判断为稳定偏好"

# Step 4: 查看摘要
python src/growth/proposal_review.py summary
```

### 5.2 批量处理建议(非自动)

> ⚠️ **明确禁止脚本自动批处理**

人工批量 review 时:

1. 按 `proposal_type` 分组
2. 优先 review 高 confidence / 高 priority
3. 逐条记录 reason(供后续审计)
4. 每次操作后查看 summary 确认

---

## 6. 审计与日志

### 6.1 审计字段

每次 review 操作记录到 proposal JSON:

```json
{
  "status": "approved",
  "decision": "approved",
  "reviewer_id": "human_operator",
  "review_comment": "证据充分,批准",
  "reviewed_at": "2026-08-02T...",
  "review_history": [
    {
      "decision": "approved",
      "reviewer_id": "human_operator",
      "review_comment": "证据充分,批准",
      "reviewed_at": "2026-08-02T..."
    }
  ]
}
```

**字段说明:**

| 字段 | 类型 | 必填 | 说明 |
| --- | --- | --- | --- |
| `status` | str | ✅ | 当前状态(pending / approved / rejected / archived) |
| `decision` | str | ✅(review 后) | review 决定(approved / rejected / archived / null) |
| `reviewer_id` | str | ✅ | 人类 reviewer ID(禁止 script/auto/agent/bot/system/llm) |
| `review_comment` | str | 推荐 | 评论(reject 时必填) |
| `reviewed_at` | str | ✅ | ISO 8601 时间戳 |
| `review_history` | list | 自动 | 每次 review 的历史记录(追加,不可删) |

### 6.1.1 4 状态规范(冻结)

| Status | 含义 | 转换源 | 触发 |
| --- | --- | --- | --- |
| `pending` | 等待人工 review | 初始 / reset | GrowthEvaluator 自动创建 |
| `approved` | 已批准(待 apply) | pending | 人工 approve |
| `rejected` | 已拒绝(不 apply) | pending | 人工 reject |
| `archived` | 已归档(过期) | pending | 人工 archive |

**状态机:**

```
   ┌──────────┐
   │ pending  │ ← reset_to_pending (人工纠错)
   └────┬─────┘
        │ 人工 review
        ├─→ approved
        ├─→ rejected
        └─→ archived
```

**禁止操作:**

- ❌ approved → pending(自动回退):必须先 reset
- ❌ pending → approved(自动):必须由人类 reviewer 显式 approve
- ❌ 任何脚本绕过 review 直接设置 status

### 6.1.2 重复 review 防护

已处于 `approved` / `rejected` / `archived` 状态的 proposal:

- 默认拒绝再次 review
- 如需重新 review,必须显式调用 `reset_to_pending()`
- 每次 reset 都会追加到 `review_history`
- `review_history` 永不删除(完整审计)

### 6.2 日志路径

- **操作日志:** `.cache/audit/proposal_review_log.jsonl`
- **持久化数据:** `data/growth/proposals/proposals.json`
- **报告:** `docs/audit/runtime_health_report.md`(pending 数 + rejected 数)

### 6.3 可查询

```bash
# 查询某 reviewer 的所有 review 操作
grep '"reviewer_id": "human_operator"' data/growth/proposals/proposals.json

# 查询所有 reject 的 comment
grep '"status": "rejected"' data/growth/proposals/proposals.json
```

---

## 7. 边界与禁止

### 7.1 明确禁止

- ❌ 任何脚本自动调用 `approve` 命令
- ❌ 修改 `auto_accept_enabled=True`
- ❌ 跳过 `--reviewer` 字段
- ❌ 跳过 `--comment`(reject 时)
- ❌ 批量通过未逐条查看
- ❌ 通过其他路径直接修改 `proposal.status`(必须走 `proposal_review.py`)

### 7.2 明确允许

- ✅ 人工逐条 review
- ✅ 通过 CLI 工具操作
- ✅ 记录完整 comment
- ✅ 审计操作历史

---

## 8. 关联文档

| 文档 | 用途 |
| --- | --- |
| [docs/audit/runtime_trial_plan.md](./audit/runtime_trial_plan.md) | Phase C.4.6.1 试验计划 |
| [docs/audit/selfmodel_runtime_daily.md](./audit/selfmodel_runtime_daily.md) | SelfModel 每日状态 |
| [scripts/selfmodel_runtime_monitor.py](../scripts/selfmodel_runtime_monitor.py) | SelfModel 监控 |
| [scripts/runtime_health_monitor.py](../scripts/runtime_health_monitor.py) | Runtime 监控 |
| [src/growth/proposal_review.py](../src/growth/proposal_review.py) | Review 工具实现 |
| [docs/growth_proposal_lifecycle.md](./growth_proposal_lifecycle.md) | Proposal schema 详细说明 |

---

## 9. 总结

Phase C.4.6.3 提供了 **强制人工 review** 的工作流:

- ✅ Proposal 生命周期定义清晰
- ✅ 状态机完整(pending → approved/rejected/archived)
- ✅ Review 工具就位(C.4.3)
- ✅ 审计字段强制完整
- ✅ 风险控制(禁止 auto-accept,禁止绕过 review)
- ✅ 与 SelfModel update 解耦

**⏸️ 等待 182 条 pending proposal 的实际 review 操作。**

---

> **生成者:** `docs/growth_review_process.md` (Phase C.4.6.3)
> **生成时间:** 2026-08-02
> **下一阶段:** Phase C.4.6.4 — First SelfModel Validation
